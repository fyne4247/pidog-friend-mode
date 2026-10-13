"""Offline Vosk input for the OpenClaw PiDog controller.

This module only submits recognized user text. It never controls hardware or
speaks; replies are handled explicitly by the agent's ``say`` command.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

_LOG = logging.getLogger(__name__)


class WakeWordGate:
    """Filter transcripts through a wake-word gate.

    A transcript containing the wake phrase and additional words is dispatched
    immediately after removing the phrase. A wake-only transcript authorizes
    exactly one later utterance, for at most ``timeout`` seconds.
    """

    def __init__(
        self,
        wake_word: str = "hey buddy",
        timeout: float = 30.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not isinstance(wake_word, str) or not wake_word.strip():
            raise ValueError("wake_word must be a non-empty string")
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        self.wake_word = wake_word.strip()
        self.timeout = float(timeout)
        self._clock = clock
        self._armed_at: float | None = None
        self._wake_re = re.compile(
            r"(?<!\w)" + re.escape(self.wake_word) + r"(?!\w)", re.IGNORECASE
        )

    @staticmethod
    def _text(result: Any) -> str:
        """Extract final text from common Vosk result shapes."""
        if isinstance(result, str):
            return result.strip()
        if isinstance(result, dict):
            if result.get("done") is False or result.get("final") is False:
                return ""
            # Vosk wrappers have returned either a plain text mapping or a
            # record containing a nested final result. Ignore partials.
            for key in ("text", "final", "done"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, dict):
                    nested = WakeWordGate._text(value)
                    if nested:
                        return nested
        return ""

    def process(self, result: Any, *, now: float | None = None) -> str | None:
        """Return an authorized utterance, or ``None`` when still gated."""
        text = self._text(result)
        if not text:
            return None
        current = self._clock() if now is None else now
        matches = list(self._wake_re.finditer(text))
        if matches:
            # Remove all standalone mentions, allowing punctuation/fillers to
            # collapse cleanly while preserving the user's remaining words.
            text = self._wake_re.sub(" ", text)
            text = re.sub(r"\s+", " ", text).strip(" \t\r\n,.;:!?-")
            self._armed_at = None
            if text:
                return text
            self._armed_at = current
            return None

        if self._armed_at is None:
            return None
        if current - self._armed_at > self.timeout:
            self._armed_at = None
            return None
        self._armed_at = None
        return text


def run_voice(
    submit: Callable[[str], Any],
    stop_event: threading.Event,
    speaking_event: threading.Event,
    *,
    language: str = "en-us",
    wake_word: str = "hey buddy",
) -> None:
    """Listen offline and submit authorized utterances as text.

    Vosk and its audio dependencies are imported only when this loop starts.
    The controller can run this function on a daemon thread because some audio
    backends block inside ``listen``; use a daemon thread if prompt shutdown is
    needed. ``stop``/``close`` are called on exit when available. Increment
    ``speaking_event.generation`` before setting it when speech starts; this
    lets the loop discard captures even when playback starts and ends during
    one blocking listen call.
    """
    listener = None
    gate = WakeWordGate(wake_word=wake_word)
    try:
        from pidog.stt import Vosk

        listener = Vosk(language=language)
        while not stop_event.is_set():
            if speaking_event.is_set():
                stop_event.wait(0.05)
                continue
            speech_generation = getattr(speaking_event, "generation", 0)
            result = listener.listen(stream=False)
            if (
                speaking_event.is_set()
                or getattr(speaking_event, "generation", 0) != speech_generation
                or stop_event.is_set()
            ):
                # A wake word spoken by Piper must not arm the next capture.
                gate = WakeWordGate(wake_word=wake_word)
                continue
            text = gate.process(result)
            if text:
                submit(text)
    except Exception as exc:
        _LOG.error("Voice input stopped after %s", type(exc).__name__)
        raise
    finally:
        if listener is not None:
            for method_name in ("stop", "close"):
                method = getattr(listener, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception as exc:
                        _LOG.error("Could not %s Vosk listener: %s", method_name, type(exc).__name__)
