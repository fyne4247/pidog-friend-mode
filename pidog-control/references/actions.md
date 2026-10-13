# Agent-selected movements

| Command | Implementation |
| --- | --- |
| stand, sit, lie | Matching posture in `Pidog.do_action` |
| forward, backward, turn-left, turn-right, trot | One finite gait cycle per count |
| wag-tail | `wag_tail` joint sequence |
| nod | `head_up_down` joint sequence |
| shake-head, tilt-head-left, tilt-head-right | Matching head joint sequences |
| stretch, push-up | Matching joint sequences |
| scratch, handshake, high-five, lick-hand | Finite silent preset choreography |

Run `python3 scripts/pidog_ctl.py action NAME --speed 60 --count 1`.
Basic motions accept counts 1–5; composite gestures run once. Composite gestures
manage their necessary body/head positions internally. The agent selects the
gesture as a whole; no unrelated posture is appended by the controller.

There are no sound-effect commands. `say "text"` is Piper speech.
`stop-motion` clears motion queues without adding a posture.
