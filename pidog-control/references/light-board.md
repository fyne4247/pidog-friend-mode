# Agent-selected lights

Only explicit `light` commands change LEDs during friend mode. There are no
wake, listening, thinking, speaking or touch-triggered light cues.

```bash
python3 scripts/pidog_ctl.py light breath --color purple
python3 scripts/pidog_ctl.py light listen --color yellow
python3 scripts/pidog_ctl.py light boom --color '#00aaff'
python3 scripts/pidog_ctl.py light off
```

`off` sets a static black frame. `solid` remains a compatibility alias for
`boom`, not a guaranteed steady-on mode. Colors accept red, green, blue, yellow,
purple, pink, cyan, white, orange, black, or hex RGB. `--brightness` is 0–1 and
`--bps` is 0.1–10. The controller validates values before touching hardware.
Use the controller commands to preserve single hardware ownership.
