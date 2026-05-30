# Hardenloop — pack-msteams-diarization-cutover (#394)

**Status:** BLOCKED on operator-gated upstream phases.

The `hardenloop` skill runs adversarial fault-injection and load tests
against a fully-deployed pack lane. It requires:

1. A running Compose lane (Phase F) — currently operator-blocked on
   `TRANSCRIPTION_SERVICE_TOKEN` re-issue.
2. A running Lite lane (Phase G) — currently operator-blocked on
   pre-existing dashboard Next.js build failure + transcription token.

Once both lanes are healthy and the human-eyeball + live-meeting gates
pass, hardenloop will:

- Throw malformed audio at the diarizer to verify the no-fallback rule
  (bot fails fast, not silently regresses to caption-driven flow).
- Force-evict the ONNX cache mid-session and confirm fail-fast.
- Replay captured Teams meetings with adversarial caption noise
  (rapid speaker flips, simultaneous overlap) and verify the
  attributor's cluster-vote stays stable.
- Run 10× consecutive bot sessions in the same container to verify
  no state leaks across sessions (pendingFrames buffer, attributor
  caption log, diarizer cluster state).

Until those gates clear, this directory holds only this placeholder.
