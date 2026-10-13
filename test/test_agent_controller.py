"""Exercise the controller over a real local socket with simulated hardware."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'pidog-control/scripts'
sys.path.insert(0, str(SCRIPTS))
import pidog_ctl as ctl
from agent_events import DurableEventQueue, SensorEventPoller


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = DurableEventQueue(Path(self.temp.name) / 'events.sqlite3')
        self.dog = Mock()
        self.dog.actions_dict = {name: ([], 'legs') for name in ctl.ACTION_MAP.values()}
        self.dog.is_all_done.return_value = True
        self.dog.dual_touch.read.return_value = 'N'
        self.dog.read_distance.return_value = -1
        self.runtime = Mock(instance=self.dog)
        self.runtime.connect.return_value = self.dog
        self.piper = Mock()
        self.controller = ctl.PiDogController(runtime=self.runtime, queue=self.queue, tts_factory=self.piper)
        self.addCleanup(self.controller.close)

    def test_sensor_events_never_choose_behavior(self):
        poller = SensorEventPoller(self.dog, self.queue)
        self.dog.dual_touch.read.return_value = 'RS'
        self.dog.read_distance.return_value = 5
        poller.poll()
        self.assertEqual([e['type'] for e in self.queue.pending()], ['touch', 'proximity'])
        self.dog.do_action.assert_not_called()
        self.dog.head_move.assert_not_called()
        self.dog.speak.assert_not_called()
        self.dog.rgb_strip.set_mode.assert_not_called()
        self.piper.assert_not_called()

    def test_explicit_movement_does_not_reset_posture(self):
        self.controller.handle({'cmd': 'action', 'name': 'wag-tail', 'count': 2})
        self.dog.do_action.assert_called_once_with('wag_tail', speed=60, step_count=2)
        self.dog.speak.assert_not_called()
        self.dog.stop_and_lie.assert_not_called()

    def test_lifecycle_postures_are_the_only_retained_automations(self):
        self.assertEqual(self.controller.startup_posture, 'sit')
        self.assertEqual(self.controller.shutdown_posture, 'lie')
        self.controller._lifecycle_posture(self.dog, self.controller.startup_posture)
        self.dog.do_action.assert_called_once_with('sit', speed=60)
        self.dog.head_move.assert_called_once_with([[0, 0, 0]], pitch_comp=-35, speed=60)
        self.controller.close()
        self.assertEqual(self.dog.do_action.call_args_list[-1].args, ('lie',))
        self.dog.tail_move.assert_called_with([[0]], speed=60)
        self.dog.speak.assert_not_called()

    def test_postures_can_be_disabled_and_close_is_idempotent(self):
        self.controller.startup_posture = 'none'
        self.controller.shutdown_posture = 'none'
        self.controller._lifecycle_posture(self.dog, 'none')
        self.controller.close()
        self.controller.close()
        self.dog.do_action.assert_not_called()
        self.runtime.close.assert_called_once_with()

    def test_speech_uses_piper_only_without_gestures(self):
        def speak(text):
            self.assertTrue(self.controller.speaking.is_set())
        self.piper.return_value.say.side_effect = speak
        self.controller.handle({'cmd': 'say', 'text': 'Hello there'})
        self.piper.assert_called_once_with(model='en_US-ryan-low')
        self.piper.return_value.say.assert_called_once_with('Hello there')
        self.assertFalse(self.controller.speaking.is_set())
        self.assertEqual(self.controller.speaking.generation, 1)
        self.dog.speak.assert_not_called()
        self.dog.do_action.assert_not_called()
        self.dog.rgb_strip.set_mode.assert_not_called()

    def test_piper_failure_has_no_fallback(self):
        self.piper.side_effect = RuntimeError('unavailable')
        with self.assertRaisesRegex(RuntimeError, 'unavailable'):
            self.controller.say('hello')
        self.assertFalse(self.controller.speaking.is_set())
        self.dog.speak.assert_not_called()

    def test_invalid_commands_do_not_touch_hardware(self):
        for request in ({'cmd': 'action', 'name': 'bark'},
                        {'cmd': 'action', 'name': 'wag-tail', 'count': 0},
                        {'cmd': 'action', 'name': 'sit', 'speed': True},
                        {'cmd': 'say', 'text': ''},
                        {'cmd': 'sound', 'name': 'snoring'},
                        {'cmd': 'light', 'mode': 'breath', 'bps': float('nan')}):
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    self.controller.handle(request)
        self.runtime.connect.assert_not_called()
        self.piper.assert_not_called()

    def test_keyboard_is_agent_input_without_behavior(self):
        result = self.controller.submit('keyboard', {'text': 'wag your tail'})
        self.assertTrue(result['queued'])
        self.assertEqual(self.queue.pending()[0]['payload'], {'text': 'wag your tail'})
        self.dog.do_action.assert_not_called()

    def test_status_counts_backlog_beyond_a_single_page(self):
        for _ in range(105):
            self.queue.enqueue('touch', {'style': 'FRONT'})
        self.assertEqual(self.controller.status()['pending_events'], 105)

    def test_startup_failure_releases_hardware_lock(self):
        with patch.object(ctl, 'STATE_DIR', Path(self.temp.name)), \
                patch.object(ctl, 'SOCKET_PATH', Path(self.temp.name) / 'absent.sock'), \
                patch.object(ctl.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'OpenClaw CLI is unavailable'):
                self.controller.serve()
        self.assertIsNone(self.controller.ownership_lock)

    def test_runtime_requires_agent_control_flag(self):
        cls = Mock()
        with patch.object(ctl.importlib, 'import_module', return_value=Mock(Pidog=cls)):
            runtime = ctl.PiDogRuntime()
            runtime.connect()
        cls.assert_called_once_with(agent_control=True)

    def test_required_touch_failure_stops_controller(self):
        poller = Mock()
        poller.poll.side_effect = OSError('sensor unavailable')
        self.controller._sensors(poller)
        self.assertTrue(self.controller.stop_event.is_set())
        self.assertEqual(self.controller.sensor_error, 'OSError')
        self.dog.do_action.assert_not_called()

    def test_socket_agent_round_trip_does_not_deadlock(self):
        state = Path(self.temp.name)
        patches = [patch.object(ctl, 'STATE_DIR', state),
                   patch.object(ctl, 'SOCKET_PATH', state / 'ctl.sock'),
                   patch.object(ctl.shutil, 'which', return_value='/fake/openclaw')]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # Simulate the OpenClaw worker receiving an event and invoking its skill.
        event_sent = threading.Event()
        errors = []
        def deliver(event):
            try:
                status = ctl.controller_request({'cmd': 'ping'}, timeout=2)
                self.assertEqual(status['agent'], 'main')
                ctl.controller_request({'cmd': 'action', 'name': 'wag-tail'}, timeout=2)
                ctl.controller_request({'cmd': 'say', 'text': 'Thanks for the pet'}, timeout=2)
            except BaseException as exc:
                errors.append(exc)
            finally:
                event_sent.set()
        self.controller.worker._deliver = deliver
        thread = threading.Thread(target=self.controller.serve, daemon=True)
        thread.start()
        self.addCleanup(lambda: thread.join(4))
        self.addCleanup(self.controller.stop_event.set)
        deadline = time.monotonic() + 3
        while self.controller.sensor_thread is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(self.controller.sensor_thread)
        self.dog.do_action.assert_called_once_with('sit', speed=60)
        self.dog.do_action.reset_mock()
        self.piper.assert_not_called()
        self.dog.dual_touch.read.return_value = 'R'
        self.assertTrue(event_sent.wait(3), 'event dispatch timed out')
        self.assertFalse(errors, errors)
        deadline = time.monotonic() + 2
        while self.queue.pending() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.queue.pending(), [])
        self.dog.do_action.assert_called_once_with('wag_tail', speed=60, step_count=1)
        self.piper.return_value.say.assert_called_once_with('Thanks for the pet')
        self.assertEqual(os.stat(ctl.SOCKET_PATH).st_mode & 0o777, 0o600)
        ctl.controller_request({'cmd': 'shutdown'}, timeout=2)
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.dog.do_action.call_args_list[-1].args, ('lie',))
        self.dog.stop_and_lie.assert_not_called()


if __name__ == '__main__':
    unittest.main()
