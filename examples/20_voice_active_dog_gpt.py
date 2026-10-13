"""Start the English PiDog OpenClaw agent.

OpenClaw selects the language model and credentials. Voice input uses the
onboard Vosk/Piper path configured by ``pidog_ctl.py``.
"""

from voice_active_dog import VoiceActiveDog


def main():
    VoiceActiveDog(agent_id='main', voice=True, openclaw_cli='openclaw').run()


if __name__ == '__main__':
    main()
