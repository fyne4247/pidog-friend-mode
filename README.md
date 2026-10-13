# Pidog

Pidog Python library for Raspberry Pi.

Quick Links:

- [Pidog](#pidog)
  - [Docs](#docs)
  - [Installation](#installation)
    - [install tool](#install-tool)
    - [robot-hat library](#robot-hat-library)
    - [vilib library](#vilib-library)
    - [pidog library](#pidog-library)
    - [i2samp](#i2samp)
  - [AI voice assistant](#ai-voice-assistant)
  - [Debug records](#debug-records)
  - [About SunFounder](#about-sunfounder)
  - [Contact us](#contact-us)
  - [Credit](#credit)

----------------------------------------------

## Docs

- <https://docs.sunfounder.com/projects/pidog/en/latest/>

----------------------------------------------

## Installation

- <https://docs.sunfounder.com/projects/pidog/en/latest/python/python_start/install_all_modules.html>

### install tool

```bash
sudo apt install git python3-pip python3-setuptools python3-smbus
```

### robot-hat library

```bash
cd ~/
git clone -b 2.5.x --depth=1 https://github.com/sunfounder/robot-hat.git
cd robot-hat
sudo python3 install.py
```

### vilib library

```bash
cd ~/
git clone --depth=1 https://github.com/sunfounder/vilib.git
cd vilib
sudo python3 install.py
```

### pidog library

```bash
cd ~/
git clone https://github.com/fyne4247/pidog-friend-mode.git pidog
sudo pip3 install ~/pidog --break-system-packages
```

### i2samp

```
cd ~/pidog
sudo bash i2samp.sh
```

## AI voice assistant

Friend mode uses the onboard OpenClaw `main` agent to decide when to move,
change lights, and speak. The controller owns the hardware and exposes finite
high-level actions; OpenClaw does not have to control individual servos.

Touch readings (front, rear, and both slide directions) are mandatory agent
messages. Held touches produce one transition message, not a polling flood.
Ultrasonic proximity events also report to the agent. Events are stored locally
in SQLite, delivered serially, and retried if OpenClaw is unavailable.
Petting and proximity do not trigger a built-in gesture or avoidance routine.
There are no idle animations, wake greetings, listening/thinking light cues,
automatic reply gestures, or automatic post-conversation posture resets.
The two retained lifecycle automations are sitting at startup and lying down
at shutdown. They can be configured with `--startup-posture` and
`--shutdown-posture` (`sit`, `lie`, `stand`, or `none`).

Speech uses Piper `en_US-ryan-low` exclusively and requires the agent's explicit
`say` command. Sound assets remain in `sounds/` for possible future use, but the
agent controller blocks their playback. OpenClaw selects its own LLM and
credentials; friend mode no longer calls OpenAI directly or requires `secret.py`.
The template remains available for the standalone online LLM example.

Optional voice input uses Vosk `en-us` and a silent "Hey Buddy" wake gate.
Say the wake phrase followed by a request, or say it alone and give a request
within 30 seconds. Each new request needs the wake phrase. The controller
suppresses input captured during its own Piper speech. Replies printed by the
OpenClaw CLI are not automatically read aloud; the agent chooses when to speak.
Camera frames are not automatically sent with each utterance.

Prepare and install using [the OpenClaw handoff](pidog-control/HANDOFF.md).
On the Pi, install Python 3.11+, `robot-hat` (branch `2.5.x`), `vilib`, and this
checkout using the steps above. Complete audio setup and calibration before
starting friend mode. Run the controller as the same Unix user as OpenClaw:

```bash
python3 pidog-control/scripts/pidog_ctl.py serve --agent main --voice
```

The original English tutorial filename also starts this controller:

```bash
python3 examples/20_voice_active_dog_gpt.py
```

Once the controller is running, the agent uses its installed
[PiDog control skill](pidog-control/SKILL.md). Example commands:

```bash
python3 pidog-control/scripts/pidog_ctl.py message "Hello Buddy"
python3 pidog-control/scripts/pidog_ctl.py action wag-tail
python3 pidog-control/scripts/pidog_ctl.py say "Hello there"
python3 pidog-control/scripts/pidog_ctl.py status
python3 pidog-control/scripts/pidog_ctl.py stop
```

Touch reporting runs even without `--voice`. A touch sensor failure stops the
controller instead of silently dropping input. Pending event IDs survive
restarts; delivery is at least once, so the skill directs the agent to avoid
repeating completed actions for a retried event. See the handoff for service
installation, diagnostics, and hardware validation that must be done on the Pi.

## Debug records

```bash
sudo pip3 uninstall pidog --break -y && cd ~/pidog && sudo pip3 install . --break --no-deps --no-build-isolation
sudo pip3 uninstall robot_hat --break -y && cd ~/robot-hat && sudo pip3 install . --break --no-deps --no-build-isolation
python3 ~/pidog/examples/20_voice_active_dog_gpt.py
```

----------------------------------------------

## About SunFounder

SunFounder is a technology company focused on Raspberry Pi and Arduino open source community development. Committed to the promotion of open source culture, we strives to bring the fun of electronics making to people all around the world and enable everyone to be a maker. Our products include learning kits, development boards, robots, sensor modules and development tools. In addition to high quality products, SunFounder also offers video tutorials to help you make your own project. If you have interest in open source or making something cool, welcome to join us!

----------------------------------------------

## Contact us

website:
    www.sunfounder.com

E-mail:
    service@sunfounder.com, support@sunfounder.com

## Credit

Most sound effect are from [Zapsplat.com](https://www.zapsplat.com)
