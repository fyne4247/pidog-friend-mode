# Troubleshooting

Inspect `status` and `journalctl --user -u pidog-agent.service`, or the foreground
controller output. A background `start` writes `~/.openclaw/pidog-control/controller.log`.

- **Import/agent_control errors:** install this fork in the controller's Python
  environment; stock PiDog lacks the new agent-control flag. Python 3.11+ is
  required by the existing enum code.
- **Missing controller socket:** start the user service/foreground controller.
  Commands never fall back to direct hardware access.
- **OpenClaw CLI unavailable:** configure an absolute `--openclaw-cli` path and
  run as the account owning OpenClaw. Confirm Gateway/model credentials and
  `openclaw agent --agent main --message "test" --json` in that account.
- **Pending events:** failed turns are retained and retried. `status` includes
  the oldest sanitized error. Check Gateway health and skill/exec permissions.
- **Required sensor reporting failure:** check touch wiring/GPIO permissions.
  The controller exits rather than continuing without petting messages.
- **Voice input stopped:** verify Vosk model, microphone and audio permissions.
  Voice-enabled operation exits if the audio input loop fails.
- **No speech:** the agent must explicitly use `say`. Check Piper model/audio
  setup and speaker enablement. No alternate speech engine is used on failure.
- **Unexpected motion:** keep manual demos and direct hardware scripts stopped.
  Use only the skill's controller commands. Startup sit and shutdown lie
  are the two intentionally retained posture automations. Inspect agent history for deliberate
  decisions; touch events themselves never select a motion.
- **Duplicate controller:** stop the existing service/process. The hardware lock
  is separate from the socket and prevents two agent-mode owners.

`solid` is an alias for `boom`, not a true steady light. LEDs change only through
explicit agent commands. `say "bark"` speaks the word through Piper; it does not
play a bark recording. `action bark` is deliberately unavailable.
