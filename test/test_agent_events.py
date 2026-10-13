import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "pidog-control" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from agent_events import (  # noqa: E402
    DurableEventQueue,
    OpenClawEventWorker,
    SensorEventPoller,
)


class FakeTouch:
    def __init__(self):
        self.values = []

    def read(self):
        return self.values.pop(0)


class FakeDog:
    def __init__(self):
        self.dual_touch = FakeTouch()
        self.distances = []
        self.actions = []
        self.sounds = []

    def read_distance(self):
        return self.distances.pop(0)

    def do_action(self, name):
        self.actions.append(name)

    def speak(self, name):
        self.sounds.append(name)


class FakeProcess:
    def __init__(self, returncode=0, stdout="", stderr="", delay=0):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.delay = delay
        self.terminated = False
        self.killed = False

    def communicate(self, timeout=None):
        if self.delay:
            time.sleep(min(timeout or 0, self.delay))
            self.delay -= min(timeout or 0, self.delay)
            if self.delay > 0 and not self.terminated:
                raise subprocess.TimeoutExpired("openclaw", timeout)
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class AgentEventsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = DurableEventQueue(Path(self.tmp.name) / "events.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_touch_events_are_transition_only_and_sensor_polling_has_no_actions(self):
        dog = FakeDog()
        dog.dual_touch.values = ["N", "L", "L", "LS", "LS", "N", "R"]
        dog.distances = [-1] * len(dog.dual_touch.values)
        poller = SensorEventPoller(dog, self.queue)

        for _ in range(7):
            poller.poll()

        events = self.queue.pending()
        self.assertEqual([e["type"] for e in events], ["touch"] * 3)
        self.assertEqual([e["payload"] for e in events], [
            {"style": "REAR", "raw": "L"},
            {"style": "REAR_TO_FRONT", "raw": "LS"},
            {"style": "FRONT", "raw": "R"},
        ])
        self.assertEqual(dog.actions, [])
        self.assertEqual(dog.sounds, [])

    def test_proximity_entry_hysteresis_and_invalid_distance(self):
        dog = FakeDog()
        dog.dual_touch.values = ["N"] * 9
        dog.distances = [9.5, 9.0, 10.5, -1, 0, 11.9, 12, 10, 9]
        poller = SensorEventPoller(dog, self.queue)
        for _ in range(9):
            poller.poll()
        events = self.queue.pending()
        self.assertEqual([e["payload"]["distance_cm"] for e in events], [9.5, 9.0])
        self.assertEqual([e["type"] for e in events], ["proximity"] * 2)

    def test_queue_is_durable_fifo_and_only_ack_removes(self):
        first = self.queue.enqueue("touch", {"style": "L"})
        second = self.queue.enqueue("proximity", {"distance_cm": 8.2})
        self.queue.record_failure(first, "CalledProcessError(returncode=1)")

        reopened = DurableEventQueue(Path(self.tmp.name) / "events.sqlite3")
        pending = reopened.pending()
        self.assertEqual([event["id"] for event in pending], [first, second])
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertEqual(pending[0]["last_error"], "CalledProcessError(returncode=1)")
        self.assertTrue(reopened.acknowledge(first))
        self.assertFalse(reopened.acknowledge(first))
        self.assertEqual([event["id"] for event in reopened.pending()], [second])

    def test_failed_delivery_retains_same_id_for_later_retry(self):
        event_id = self.queue.enqueue("touch", {"style": "RS"})
        first_failed = threading.Event()

        attempts = []

        def fail_once(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertNotIn("--deliver", argv)
            self.assertNotIn("--local", argv)
            attempts.append(argv)
            if len(attempts) == 1:
                first_failed.set()
                return FakeProcess(returncode=1, stdout="private transcript")
            return FakeProcess()

        worker = OpenClawEventWorker(
            self.queue, retry_backoff=10, process_factory=fail_once
        )
        worker.start()
        self.assertTrue(first_failed.wait(2))
        deadline = time.monotonic() + 2
        while self.queue.pending()[0]["attempts"] == 0 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(worker.stop(timeout=1))
        pending = self.queue.pending()
        self.assertEqual(pending[0]["id"], event_id)
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertNotIn("private transcript", pending[0]["last_error"])

        delivered = []

        def succeed(argv, **kwargs):
            delivered.append((argv, kwargs))
            return FakeProcess(stdout='{"ok":true}')

        retry = OpenClawEventWorker(self.queue, process_factory=succeed)
        retry.start()
        deadline = time.monotonic() + 2
        while self.queue.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(retry.stop(timeout=1))
        self.assertFalse(self.queue.pending())
        self.assertEqual(len(delivered), 1)
        argv, kwargs = delivered[0]
        self.assertEqual(argv[:4], ["openclaw", "agent", "--agent", "main"])
        self.assertIn("--json", argv)
        self.assertEqual(argv[-2:], ["--timeout", "120"])
        self.assertEqual(kwargs, {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        })
        message = argv[argv.index("--message") + 1]
        self.assertEqual(json.loads(message.split("\n", 1)[1])["event_id"], event_id)

    def test_worker_is_serial_and_configuration_is_validated(self):
        with self.assertRaises(ValueError):
            OpenClawEventWorker(self.queue, openclaw_cli=" ")
        with self.assertRaises(ValueError):
            OpenClawEventWorker(self.queue, agent_id=" ")

        self.queue.enqueue("touch", {"style": "L"})
        self.queue.enqueue("touch", {"style": "R"})
        lock = threading.Lock()
        active = 0
        maximum = 0
        calls = []

        def slow_runner(argv, **kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
                calls.append(argv)
            class SerialProcess(FakeProcess):
                def communicate(self, timeout=None):
                    try:
                        time.sleep(0.03)
                        return super().communicate(timeout)
                    finally:
                        with lock:
                            nonlocal active
                            active -= 1

            return SerialProcess()

        worker = OpenClawEventWorker(self.queue, process_factory=slow_runner)
        worker.start()
        deadline = time.monotonic() + 2
        while self.queue.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(worker.stop(timeout=1))
        self.assertEqual(maximum, 1)
        self.assertEqual(len(calls), 2)

    def test_stop_terminates_active_cli_and_retains_unknown_delivery(self):
        self.queue.enqueue("touch", {"style": "L"})
        spawned = threading.Event()
        processes = []

        def hanging_process(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertNotIn("shell", kwargs)
            process = FakeProcess(returncode=None, delay=60)
            processes.append(process)
            spawned.set()
            return process

        worker = OpenClawEventWorker(self.queue, process_factory=hanging_process)
        worker.start()
        self.assertTrue(spawned.wait(1))
        started = time.monotonic()
        self.assertTrue(worker.stop())
        self.assertLess(time.monotonic() - started, 3.1)
        self.assertTrue(processes[0].terminated)
        pending = self.queue.pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["attempts"], 1)
        self.assertEqual(pending[0]["last_error"], "DeliveryInterrupted(completion=unknown)")

    def test_poller_requires_touch_sensor(self):
        with self.assertRaisesRegex(RuntimeError, "dual_touch.read"):
            SensorEventPoller(object(), self.queue)

    def test_queue_files_have_private_permissions(self):
        parent_mode = self.queue.path.parent.stat().st_mode & 0o777
        db_mode = self.queue.path.stat().st_mode & 0o777
        self.assertEqual(parent_mode, 0o700)
        self.assertEqual(db_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
