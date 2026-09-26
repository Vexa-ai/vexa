- **Raw-audio tapes are off unless you turn them on (#0000).** Every bot spawn kept a
  captured-signal tape — raw audio, STT round-trips, transcript, bot log — under `signal/` in your
  object store, recorded meeting or not, although earlier notes called this off by default. It now
  runs only with `CAPTURE_SIGNAL_ENABLED=true` (Helm `meetingApi.captureSignalEnabled`) or an
  identity `diagnostics.capture_signal` setting. Tapes already written stay under `signal/` until
  you delete them or the 50 GiB budget evicts them — deleting a meeting does not remove its tape. See
  [Configuration](/configuration#transcription-stt).
- **Claude Code in the agent images stops calling home (#0000).** The agent worker and Lite images
  turn off its telemetry, error reporting and update checks. The calls a stock install still makes
  are listed in [Security & compliance](/security-compliance#what-calls-out-by-default).
- **Compose ships no admin token (#0000).** `ADMIN_TOKEN` is empty in `.env.example`, and
  `make all` / `make dev` generate it along with every other empty stack secret. admin-api and
  meeting-api refuse to boot on `dev-admin-token`: if your `.env` still has it, replace it before
  upgrading (`openssl rand -hex 32`). API keys you already issued keep working.
- **agent-api has no default user on Compose or Helm (#0000).** `VEXA_AGENT_DEFAULT_SUBJECT` was
  `u_live`, which answered every request without `X-User-Id` as that one user. It is now unset, so
  such a request is refused (401). See [Identity](/core/identity).
