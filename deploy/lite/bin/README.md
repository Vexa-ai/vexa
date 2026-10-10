# deploy/lite/bin — lite container helper scripts

In-image scripts for the single-container [lite](../README.md) deployment. Copied to
`/usr/local/bin` (or invoked by supervisord) inside `vexa-lite:dev`.

| Script | Role |
|---|---|
| `vexa-bot-launch` | meeting-bot launcher the runtime execs per meeting via the **process backend** (`BOT_COMMAND`). Starts the bot's own PulseAudio daemon (its `tts_sink → virtual_mic` graph, in its private HOME) and runs the bot worker against the shared Xvfb display, whose cookie its `vexa-display` group reads. |
| `vexa-runtime` | supervisord's runtime launcher: starts the runtime from a cleared environment — the keys its config.v1 declaration names plus host plumbing — so no service secret of the instance reaches the runtime or any bot or worker it spawns. |
| `vexa-agent-worker` | agent-worker launcher the runtime execs per dispatch (`AGENT_WORKER_COMMAND`) — the claude-in-process turn under the agent venv. |
| `display-cookie` | the entrypoint's: writes a fresh X authority cookie for the shared display at each start, readable by root and the `vexa-display` group only (Xvfb runs with access control on). |
| `render-supervisord` | the entrypoint's: writes the root-only copy of the supervisor config supervisord runs, with the runtime caller credential in the `environment=` of the runtime, agent-api and meeting-api only. |
| `persisted-secret` | prints the secret kept in a file, minting and saving one (0600) on first use — the entrypoint's source for `NEXTAUTH_SECRET` and `VEXA_DISPATCH_SIGNING_KEY` when none is given, under `$VEXA_LITE_STATE_DIR` (default `/var/lib/vexa/state`). |
| `provision-key.sh` | background (from the entrypoint): mints a self-host API key once admin-api is up and hands it to the dashboard + terminal for zero-login. No-op if `VEXA_API_KEY` is supplied. |
