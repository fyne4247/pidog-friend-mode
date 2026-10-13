# Controller API

The only hardware owner is `scripts/pidog_ctl.py serve`.
It creates `Pidog(agent_control=True)` with no initial servo-angle writes,
blocks `speak`/`speak_block` sound effects, and closes without its legacy parking routine. The controller explicitly
performs the retained lifecycle postures: sit at startup, lie at shutdown.
Legacy manually launched tutorials use the original `Pidog()` behavior.

A private Unix socket at `~/.openclaw/pidog-control/controller.sock` accepts one
JSON line per connection. Commands: `ping`, `action`, `light`, `say`, `event`,
`stop-motion`, `shutdown`. `event` accepts voice/keyboard text; touch/proximity
are read centrally by the controller. Responses are JSON lines containing
`ok` and `data` or `error`. No network port is opened.

Successful `action` responses confirm completion. Named basic movements use
`dog.do_action`; silent gestures use finite `preset_actions` functions.
No ActionFlow idle behavior or text `ACTIONS:` parsing is used.
`Piper.say(text)` is the sole output-audio path.

Event records are stored in SQLite and sent to the configured agent using
`openclaw agent --agent main --message ... --json --timeout 120`.
The event worker runs separately from sensor polling and socket handlers so
an agent can invoke the controller while its event turn is in progress.
A failed or interrupted turn retains its event UUID for retry. There is no
automatic projection of an agent's CLI text reply into speech or movements.
