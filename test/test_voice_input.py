import importlib.util
import pathlib
import sys
import threading
import types
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(__file__).parents[1] / "pidog-control" / "scripts" / "voice_input.py"
spec = importlib.util.spec_from_file_location("voice_input", SCRIPT)
voice_input = importlib.util.module_from_spec(spec)
_previous_voice_input = sys.modules.get(spec.name)
sys.modules[spec.name] = voice_input
spec.loader.exec_module(voice_input)
if _previous_voice_input is None:
    del sys.modules[spec.name]
else:
    sys.modules[spec.name] = _previous_voice_input
WakeWordGate = voice_input.WakeWordGate


class WakeWordGateTests(unittest.TestCase):
    def test_inline_wake_phrase_dispatches_immediately_and_is_removed(self):
        gate = WakeWordGate()
        self.assertEqual(gate.process("Hey Buddy, what time is it?"), "what time is it")
        self.assertIsNone(gate.process("what is the weather"))

    def test_wake_only_arms_for_one_utterance(self):
        gate = WakeWordGate(timeout=30)
        self.assertIsNone(gate.process("HEY BUDDY", now=10))
        self.assertEqual(gate.process({"text": "Turn on the light"}, now=40), "Turn on the light")
        self.assertIsNone(gate.process("and another thing", now=40))

    def test_wake_phrase_requires_word_boundaries(self):
        gate = WakeWordGate()
        self.assertIsNone(gate.process("hey buddyish, hello"))
        self.assertEqual(gate.process("Hey Buddy! Dance"), "Dance")

    def test_expired_gate_does_not_authorize_utterance(self):
        gate = WakeWordGate(timeout=30)
        gate.process("hey buddy", now=0)
        self.assertIsNone(gate.process("please help", now=30.01))
        self.assertIsNone(gate.process("still listening", now=30.02))

    def test_partial_vosk_records_are_ignored_even_with_text(self):
        gate = WakeWordGate()
        self.assertIsNone(gate.process({"done": False, "text": "hey buddy"}))
        self.assertIsNone(gate.process({"final": False, "text": "hey buddy"}))
        self.assertIsNone(gate.process("request without wake"))

    def test_ignores_capture_that_overlaps_piper_speech(self):
        speaking = threading.Event()
        stop = threading.Event()
        submitted = []

        class FakeVosk:
            def __init__(self, language):
                self.calls = 0

            def listen(self, stream=False):
                self.calls += 1
                speaking.set()  # Piper started while this capture was open.
                stop.set()
                return "hey buddy do something"

            def close(self):
                pass

        pidog = types.ModuleType("pidog")
        stt = types.ModuleType("pidog.stt")
        stt.Vosk = FakeVosk
        with patch.dict(sys.modules, {"pidog": pidog, "pidog.stt": stt}):
            voice_input.run_voice(submitted.append, stop, speaking)
        self.assertEqual(submitted, [])

    def test_discards_capture_when_playback_starts_and_ends_during_listen(self):
        class SpeakingEvent(threading.Event):
            generation = 0

        speaking = SpeakingEvent()
        stop = threading.Event()
        submitted = []

        class FakeVosk:
            def __init__(self, language):
                pass

            def listen(self, stream=False):
                speaking.generation += 1
                speaking.set()
                speaking.clear()
                stop.set()
                return "hey buddy"

            def close(self):
                pass

        stt = types.ModuleType("pidog.stt")
        stt.Vosk = FakeVosk
        with patch.dict(
            sys.modules,
            {"pidog": types.ModuleType("pidog"), "pidog.stt": stt},
        ):
            voice_input.run_voice(submitted.append, stop, speaking)
        self.assertEqual(submitted, [])

    def test_listener_errors_propagate(self):
        class FakeVosk:
            def __init__(self, language):
                pass

            def listen(self, stream=False):
                raise RuntimeError("backend details")

            def close(self):
                pass

        stt = types.ModuleType("pidog.stt")
        stt.Vosk = FakeVosk
        with patch.dict(
            sys.modules,
            {"pidog": types.ModuleType("pidog"), "pidog.stt": stt},
        ):
            with self.assertRaisesRegex(RuntimeError, "backend details"):
                voice_input.run_voice(
                    lambda text: None, threading.Event(), threading.Event()
                )

    def test_wake_then_utterance_submits_and_closes_listener(self):
        stop = threading.Event()
        submitted = []

        class FakeVosk:
            def __init__(self, language):
                self.asserted_language = language
                self.results = iter(["hey buddy", {"done": True, "text": "Tell me a joke"}])
                self.stopped = False
                self.closed = False

            def listen(self, stream=False):
                return next(self.results)

            def stop(self):
                self.stopped = True

            def close(self):
                self.closed = True

        listeners = []

        def build_listener(language):
            listener = FakeVosk(language)
            listeners.append(listener)
            return listener

        def submit(text):
            submitted.append(text)
            stop.set()

        stt = types.ModuleType("pidog.stt")
        stt.Vosk = build_listener
        with patch.dict(
            sys.modules,
            {"pidog": types.ModuleType("pidog"), "pidog.stt": stt},
        ):
            voice_input.run_voice(submit, stop, threading.Event(), language="en-us")

        self.assertEqual(submitted, ["Tell me a joke"])
        self.assertEqual(len(listeners), 1)
        self.assertEqual(listeners[0].asserted_language, "en-us")
        self.assertTrue(listeners[0].stopped)
        self.assertTrue(listeners[0].closed)


if __name__ == "__main__":
    unittest.main()
