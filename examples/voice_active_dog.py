"""Compatibility facade for starting PiDog's OpenClaw agent service."""

import os
from pathlib import Path
import sys


class VoiceActiveDog:
    """Launch the local PiDog controller under OpenClaw."""

    def __init__(self, agent_id='main', voice=True, openclaw_cli='openclaw'):
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError('agent_id must be a non-empty string')
        if not isinstance(openclaw_cli, str) or not openclaw_cli.strip():
            raise ValueError('openclaw_cli must be a non-empty string')
        if not isinstance(voice, bool):
            raise ValueError('voice must be a boolean')

        self.agent_id = agent_id.strip()
        self.voice = voice
        self.openclaw_cli = openclaw_cli.strip()

    def run(self):
        repo_root = Path(__file__).resolve().parents[1]
        controller = repo_root / 'pidog-control' / 'scripts' / 'pidog_ctl.py'
        argv = [
            sys.executable,
            str(controller),
            'serve',
            '--agent', self.agent_id,
            '--openclaw-cli', self.openclaw_cli,
        ]
        if self.voice:
            argv.append('--voice')
        os.execv(sys.executable, argv)
