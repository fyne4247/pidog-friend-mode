"""Durable sensor-event delivery from the PiDog controller to OpenClaw.

This module only transports events and reads sensors. It never selects or
performs a movement, plays a sound, or speaks on the robot's behalf.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


DEFAULT_QUEUE_PATH = Path.home() / ".openclaw" / "pidog-control" / "events.sqlite3"
TOUCH_STYLES = {"L", "R", "LS", "RS"}
TOUCH_STYLE_NAMES = {
    "L": "REAR",
    "R": "FRONT",
    "LS": "REAR_TO_FRONT",
    "RS": "FRONT_TO_REAR",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class DurableEventQueue:
    """A small SQLite FIFO; unacknowledged records remain available on restart."""

    def __init__(self, path: str | Path = DEFAULT_QUEUE_PATH):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )"""
            )
        os.chmod(self.path, 0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(str(self.path), timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def count(self) -> int:
        with self._lock, self._connect() as db:
            return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def enqueue(self, event_type: str, payload: dict[str, Any]) -> str:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("event_type must be a non-empty string")
        if not isinstance(payload, dict):
            raise TypeError("payload must be a dictionary")
        event_id = str(uuid.uuid4())
        with self._lock, self._connect() as db:
            db.execute(
                "INSERT INTO events (id, created_at, type, payload) VALUES (?, ?, ?, ?)",
                (event_id, _utc_now(), event_type, json.dumps(payload, ensure_ascii=False)),
            )
        return event_id

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock, self._connect() as db:
            rows = db.execute(
                "SELECT id, created_at, type, payload, attempts, last_error "
                "FROM events ORDER BY rowid LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "type": row["type"],
                "payload": json.loads(row["payload"]),
                "attempts": row["attempts"],
                "last_error": row["last_error"],
            }
            for row in rows
        ]

    def acknowledge(self, event_id: str) -> bool:
        """Remove an event only after successful delivery; return whether it existed."""
        with self._lock, self._connect() as db:
            cur = db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        return cur.rowcount == 1

    def record_failure(self, event_id: str, error: str) -> bool:
        """Retain the event and update retry metadata without storing output/transcripts."""
        safe_error = (error or "delivery failed")[:300]
        with self._lock, self._connect() as db:
            cur = db.execute(
                "UPDATE events SET attempts = attempts + 1, last_error = ? WHERE id = ?",
                (safe_error, event_id),
            )
        return cur.rowcount == 1


def _safe_process_error(exc: BaseException) -> str:
    # Exception strings from subprocess APIs can contain argv or captured output.
    if isinstance(exc, subprocess.CalledProcessError):
        return f"CalledProcessError(returncode={exc.returncode})"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "TimeoutExpired(timeout=150)"
    if isinstance(exc, DeliveryInterrupted):
        return "DeliveryInterrupted(completion=unknown)"
    return type(exc).__name__


class DeliveryInterrupted(Exception):
    """The OpenClaw CLI was stopped before its completion could be confirmed."""


class OpenClawEventWorker:
    """Deliver queued events in order on one worker thread, with retry backoff."""

    def __init__(
        self,
        queue: DurableEventQueue,
        *,
        openclaw_cli: str = "openclaw",
        agent_id: str = "main",
        retry_backoff: float = 5.0,
        process_factory: Callable[..., Any] | None = None,
    ):
        self.queue = queue
        self.openclaw_cli = openclaw_cli.strip() if isinstance(openclaw_cli, str) else ""
        self.agent_id = agent_id.strip() if isinstance(agent_id, str) else ""
        if not self.openclaw_cli:
            raise ValueError("OpenClaw CLI path must be non-empty")
        if not self.agent_id:
            raise ValueError("OpenClaw agent id must be non-empty")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be non-negative")
        self.retry_backoff = retry_backoff
        self._process_factory = process_factory or subprocess.Popen
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._active_process: Any = None

    @staticmethod
    def _message(event: dict[str, Any]) -> str:
        content = {
            "source": "pidog",
            "event_id": event["id"],
            "timestamp": event["created_at"],
            "type": event["type"],
            "payload": event["payload"],
        }
        return (
            "Read and follow the pidog-control skill. Choose the appropriate behavior, "
            "then explicitly invoke its control tool to enact it. Use its Piper `say` "
            "tool for speech; do not play sound assets. If this event_id is delivered "
            "again, do not repeat behavior already completed for it.\n"
            + json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        )

    def _deliver(self, event: dict[str, Any]) -> None:
        argv = [
            self.openclaw_cli,
            "agent",
            "--agent",
            self.agent_id,
            "--message",
            self._message(event),
            "--json",
            "--timeout",
            "120",
        ]
        process = self._process_factory(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._active_process = process
        deadline = time.monotonic() + 150
        try:
            while True:
                if self._stop_event.is_set():
                    self._terminate_process(process)
                    raise DeliveryInterrupted()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._terminate_process(process)
                    raise subprocess.TimeoutExpired(argv, 150)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.2, remaining))
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode != 0:
                raise subprocess.CalledProcessError(
                    process.returncode, argv, output=stdout, stderr=stderr
                )
        finally:
            self._active_process = None

    @staticmethod
    def _terminate_process(process: Any) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                return
            # Reap the killed process so it cannot outlive controller shutdown.
            process.communicate(timeout=2)

    def run(self) -> None:
        """Worker loop, also exposed for deterministic embedding/testing."""
        while not self._stop_event.is_set():
            events = self.queue.pending(limit=1)
            if not events:
                self._stop_event.wait(0.25)
                continue
            event = events[0]
            try:
                self._deliver(event)
            except Exception as exc:
                self.queue.record_failure(event["id"], _safe_process_error(exc))
                # Do not log event payload, subprocess output, or credentials.
                if not self._stop_event.is_set():
                    self._stop_event.wait(self.retry_backoff)
            else:
                self.queue.acknowledge(event["id"])

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self.run, name="pidog-openclaw-event-worker", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> bool:
        """Stop delivery promptly and leave in-flight events pending if uncertain."""
        self._stop_event.set()
        if self._thread is None:
            return True
        self._thread.join(max(0.0, timeout))
        return not self._thread.is_alive()


class SensorEventPoller:
    """Convert PiDog touch and proximity readings to transition events only."""

    def __init__(self, dog: Any, queue: DurableEventQueue):
        touch = getattr(dog, "dual_touch", None)
        if touch is None or not callable(getattr(touch, "read", None)):
            raise RuntimeError("PiDog dual_touch.read() is unavailable")
        self.dog = dog
        self.queue = queue
        self._last_touch: str | None = None
        self._in_proximity = False

    def poll(self) -> list[str]:
        """Poll once and return IDs for newly enqueued sensor events."""
        ids: list[str] = []
        raw = self.dog.dual_touch.read()
        if raw not in {"N", *TOUCH_STYLES}:
            raise RuntimeError(f"Unexpected PiDog touch reading: {raw!r}")
        if raw == "N":
            self._last_touch = None
        else:
            if raw != self._last_touch:
                ids.append(
                    self.queue.enqueue(
                        "touch", {"style": TOUCH_STYLE_NAMES[raw], "raw": raw}
                    )
                )
            self._last_touch = raw

        read_distance = getattr(self.dog, "read_distance", None)
        if callable(read_distance):
            distance = read_distance()
            try:
                distance = float(distance)
            except (TypeError, ValueError):
                distance = math.nan
            # PiDog uses <= 1 cm and non-finite/-1 readings as invalid.
            if math.isfinite(distance) and distance > 1:
                if distance < 10 and not self._in_proximity:
                    ids.append(
                        self.queue.enqueue("proximity", {"distance_cm": distance})
                    )
                    self._in_proximity = True
                elif distance >= 12:
                    self._in_proximity = False
        return ids
