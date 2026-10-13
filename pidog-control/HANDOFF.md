# Install OpenClaw PiDog friend mode

This checkout is prepared off the Pi. Hardware and actual OpenClaw integration
have not yet been validated on a Pi. The default recipient is the OpenClaw
`main` agent. Motion choreography remains high level, with all timing/behavior
decisions made by that agent.

## Prepare the Pi

Use Python 3.11 or newer. Install this fork as `~/pidog`, plus the guide's
`robot-hat` `2.5.x`, `vilib`, amplifier setup, and servo calibration, as described
in the repository README. Configure the OpenClaw Gateway, model and credentials
for `main`; run `openclaw agent --help` and confirm `--agent`, `--message`, `--json`
and `--timeout` are supported. No direct OpenAI key is required by friend mode.

Run the controller as the Unix account that owns OpenClaw and its configuration,
not through a root account's OpenClaw profile. Give that account access to the
Pi's I2C/GPIO and audio devices using your Raspberry Pi OS configuration.
Typical groups are `i2c`, `gpio`, `audio` and `video`; log out/in after changes.
Verify the guide's Piper/Vosk audio tests under this same account.
The first voice/speech use may download Vosk/Piper models.

Install the skill directory, including all three controller modules:

```bash
mkdir -p ~/.openclaw/workspace/skills
cp -R ~/pidog/pidog-control ~/.openclaw/workspace/skills/
```

If the main agent has a custom workspace, copy the skill there instead.
After code changes, update both the installed Python package and this skill
copy. `PIDOG_STATE_DIR` can override the state directory, but clients and the
controller must use the same value.

## First launch

Start the Gateway with your normal OpenClaw setup. Then, from another terminal:

```bash
python3 ~/.openclaw/workspace/skills/pidog-control/scripts/pidog_ctl.py serve --agent main --voice
```

Omit `--voice` for mandatory touch/proximity reporting with keyboard input only.
The controller sits once at startup and lies down on shutdown. These are the
two retained posture automations; there is no post-conversation reset. Use
`--startup-posture` / `--shutdown-posture` to configure `sit`, `lie`, `stand`
or `none` if this changes later.

The default voice gate is silent "Hey Buddy" and accepts one subsequent request
within 30 seconds; an inline request is accepted immediately. Speech is Piper
`en_US-ryan-low` only and requires an explicit agent command. Camera input is
not automatically captured or uploaded.

From a separate terminal, inspect the controller and submit a message:

```bash
python3 ~/.openclaw/workspace/skills/pidog-control/scripts/pidog_ctl.py status
python3 ~/.openclaw/workspace/skills/pidog-control/scripts/pidog_ctl.py message "Say hello using Piper."
```

Use `--openclaw-cli /absolute/path/to/openclaw` if it isn't on the controller's
PATH. The agent uses the installed skill's commands to enact its decisions.
No automatic nod, wag, backing up, light animation or voice greeting accompanies
input events. Ordinary agent replies are not automatically spoken.

## Run as a user service

The included unit assumes the default workspace path above:

```bash
mkdir -p ~/.config/systemd/user
cp ~/pidog/pidog-control/pidog-agent.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now pidog-agent.service
journalctl --user -u pidog-agent.service -f
```

Edit the unit's ExecStart to include `--openclaw-cli /absolute/path/to/openclaw`
if your installation uses a custom Node/npm prefix. Enable user lingering if
you want the service to survive logout. Stop any foreground/controller instance
before starting the service. The process lock rejects duplicate hardware owners.

## Event delivery and validation

The controller polls all four touch styles and emits changes once per hold.
Proximity reports entry below 10 cm, rearming at 12 cm. It never chooses a reflex.
Messages are persisted before delivery in a private SQLite database, and a
serial worker invokes OpenClaw through its Gateway-backed CLI. Failed/unknown
turns retain the same event UUID for retry. Delivery is at least once, so the
agent skill directs deduplication by event_id and checking stale timestamps.
This is not a guarantee of exactly-once physical execution after a process crash.

On the Pi, check each touch sensor and slide direction reaches `main` without a
built-in motion, verify an explicit tail wag is finite, verify Piper speech,
and verify startup sits and shutdown lies down once. Motion primitives
interpolate from their configured initial angles; servos have no feedback, so
check the first explicit move after boot with the robot supported and calibrated.
Manual tutorial programs are separate hardware owners; keep them stopped while
friend mode runs.

Sensor errors stop the service instead of silently disabling mandatory touch
reporting. `status` shows queued events and the oldest sanitized delivery error.
Logs/state live in `~/.openclaw/pidog-control/`; event payloads contain transcripts,
so that directory and database are private to the account.

## Stop

```bash
python3 ~/.openclaw/workspace/skills/pidog-control/scripts/pidog_ctl.py stop-motion
python3 ~/.openclaw/workspace/skills/pidog-control/scripts/pidog_ctl.py stop
```

Orderly shutdown finishes an in-flight Piper utterance before lying down.
A power loss or forced process kill cannot perform the shutdown posture.
For the user service, use `systemctl --user stop pidog-agent.service` instead.
Sound files remain in the checkout, but no agent-mode playback API exposes them.
