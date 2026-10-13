# Expected layout

- `~/pidog`: this fork, installed into system Python 3.11+.
- `~/robot-hat`: guide branch `2.5.x`, installed dependencies.
- `~/vilib`: camera dependency retained for manual use.
- `~/.openclaw/workspace/skills/pidog-control`: complete copied skill directory.
- `~/.openclaw/pidog-control`: private event database, socket, lock and optional log.

Run OpenClaw and the controller as the same Unix account. If the main agent has
a custom workspace, install the skill there. See `HANDOFF.md` for the user
service and device-permission setup. The controller requires this fork's
`Pidog(agent_control=True)` and does not attempt a legacy fallback.
