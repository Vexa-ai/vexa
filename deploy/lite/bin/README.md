# deploy/lite/bin — lite container helper scripts

In-image scripts for the single-container [lite](../README.md) deployment. Copied to
`/usr/local/bin` (or invoked by supervisord) inside `vexa-lite:dev`.

| Script | Role |
|---|---|
| `vexa-bot-launch` | meeting-bot launcher the runtime execs per meeting via the **process backend** (`BOT_COMMAND`). Runs the bot worker against the container's shared Xvfb/PulseAudio. |
| `vexa-agent-worker` | agent-worker launcher the runtime execs per dispatch (`AGENT_WORKER_COMMAND`) — the claude-in-process turn under the agent venv. |
| `setup-pulseaudio-sinks.sh` | one-shot: builds the `tts_sink → virtual_mic` PulseAudio graph the bot's capture/speak path expects. |
| `provision-key.sh` | background (from the entrypoint): mints a self-host API key once admin-api is up and hands it to the dashboard + terminal for zero-login. No-op if `VEXA_API_KEY` is supplied. |
