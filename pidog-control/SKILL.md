---
name: pidog-control
description: Control this PiDog through its persistent local controller, interpret touch and voice events, select finite movements and lights, and speak through Piper. All behavior decisions belong to the OpenClaw agent.
---

# PiDog control

You are the decision-maker for the physical PiDog. Sensor events are information,
not commands to run a predefined reflex.
The controller sits at startup and lies down at shutdown, as requested. Those
are the two lifecycle exceptions; do not add other automatic posture changes.
Choose whether to wag, nod, move, change lights, speak, or do nothing. The controller performs servo choreography for
explicitly selected actions. Never start an idle animation or a polling loop
that performs movements automatically.

Use the scripts relative to this skill directory. The controller must already
be running as the same Unix user as OpenClaw. All commands go through its private
Unix socket; do not instantiate `Pidog`, run tutorial demos, or start a second
hardware owner. If the controller is unavailable, report the error; don't fall
back to direct hardware access or another voice engine.

## Events

Messages contain `source: pidog`, a UUID `event_id`, a timestamp, a type and a
payload. Types include `touch`, `proximity`, `voice`, and `keyboard`.
Touch styles are `FRONT`, `REAR`, `FRONT_TO_REAR`, and `REAR_TO_FRONT`, with raw
sensor codes also included. Do not assume any particular touch is liked or hated.
Proximity contains `distance_cm`; choose an appropriate response yourself.
Voice/keyboard contain `text`; treat that as the user's request.

Delivery is at least once. Check event_id against your recent session history
and do not repeat completed movements or speech when the same event is retried.
Use the timestamp when deciding whether an old queued event is still relevant.
If appropriate, choose no physical response. Event transport's successful
completion only means your turn completed; don't claim a command succeeded
unless its controller response confirms completion.

## Commands

```bash
python3 scripts/pidog_ctl.py status
python3 scripts/pidog_ctl.py action wag-tail
python3 scripts/pidog_ctl.py action nod
python3 scripts/pidog_ctl.py action sit
python3 scripts/pidog_ctl.py action forward --speed 60 --count 1
python3 scripts/pidog_ctl.py light breath --color purple
python3 scripts/pidog_ctl.py light off
python3 scripts/pidog_ctl.py say "That feels nice."
python3 scripts/pidog_ctl.py stop-motion
```

Speech must use the `say` command, which speaks text using Piper only.
Your ordinary chat reply is not automatically spoken. Never use named sound
assets, bark/howling/pant presets, espeak, Pico2Wave, cloud TTS, or raw audio
playback. The sound files are retained for a future feature and are disabled in
friend mode today.

Movement commands are finite. `--count` is 1–5 for basic motions; gestures run
once. `--speed` is 1–100. The controller waits for movement completion, serializes
movement/speech/light commands, and keeps the resulting posture. `--hold` is a
compatibility flag; no cleanup posture is added after a command. Any required
posture transitions inside an explicitly selected gesture are part of its servo
choreography. Don't add an unrelated sit/lie, light effect, or gesture after
speech unless you choose it deliberately.

Use short movements on a stable surface with known clearance. When setup is
unknown, inspect status and report that physical calibration/clearance need to
be checked before moving. Do not run an automatic stand/sit test on receiving
an event. `stop-motion` clears queued movement without choosing a new posture.
`stop` shuts down the controller and lies down; it is an explicit administrative
action. Ordinary movement/speech commands keep their result without parking.

See [actions](references/actions.md), [installation](HANDOFF.md), and
[troubleshooting](references/troubleshooting.md).
