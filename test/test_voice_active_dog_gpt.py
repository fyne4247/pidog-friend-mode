"""Launcher tests that do not require PiDog hardware or OpenClaw."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FACADE = ROOT / 'examples' / 'voice_active_dog.py'
EXAMPLE = ROOT / 'examples' / '20_voice_active_dog_gpt.py'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EnglishAssistantLauncherTests(unittest.TestCase):
    def setUp(self):
        self.facade = load_module('voice_active_dog_test_facade', FACADE)
        self.modules_patch = patch.dict(sys.modules, {'voice_active_dog': self.facade})
        self.modules_patch.start()
        self.addCleanup(self.modules_patch.stop)
        self.example = load_module('english_openclaw_example', EXAMPLE)

    def test_import_does_not_start_controller(self):
        with patch('os.execv') as execv:
            imported = load_module('english_openclaw_import_check', EXAMPLE)
        execv.assert_not_called()
        self.assertTrue(callable(imported.main))

    def test_main_invokes_facade_with_english_defaults(self):
        with patch.object(self.example, 'VoiceActiveDog') as facade:
            self.example.main()
        facade.assert_called_once_with(agent_id='main', voice=True,
                                       openclaw_cli='openclaw')
        facade.return_value.run.assert_called_once_with()

    def test_run_execs_controller_with_configured_arguments(self):
        dog = self.facade.VoiceActiveDog(agent_id='worker-1', voice=True,
                                        openclaw_cli='openclaw-dev')
        expected_argv = [
            sys.executable,
            str(ROOT / 'pidog-control' / 'scripts' / 'pidog_ctl.py'),
            'serve', '--agent', 'worker-1', '--openclaw-cli', 'openclaw-dev',
            '--voice',
        ]
        with patch.object(self.facade.os, 'execv') as execv:
            dog.run()
        execv.assert_called_once_with(sys.executable, expected_argv)

    def test_voice_flag_is_omitted_when_disabled(self):
        dog = self.facade.VoiceActiveDog(voice=False)
        with patch.object(self.facade.os, 'execv') as execv:
            dog.run()
        argv = execv.call_args.args[1]
        self.assertNotIn('--voice', argv)

    def test_empty_or_invalid_arguments_are_rejected(self):
        for kwargs in (
                {'agent_id': ''},
                {'agent_id': '  '},
                {'agent_id': None},
                {'openclaw_cli': ''},
                {'openclaw_cli': '  '},
                {'openclaw_cli': None},
                {'voice': 'yes'}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.facade.VoiceActiveDog(**kwargs)


if __name__ == '__main__':
    unittest.main()
