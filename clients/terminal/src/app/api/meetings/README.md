# api/meetings (binary meeting routes)

The two meetings routes whose bodies are not JSON, so the catch-all `../[...path]` (which reads
every body as text and stamps JSON on it) cannot carry them:

- `[id]/export` — `GET` streams the meeting-bundle.v1 zip from the gateway with its
  `Content-Disposition`, untouched.
- `import` — `POST` streams the dropped zip to the gateway (`?dry_run=true` for the preview) and
  returns its JSON answer.

Every other `/api/meetings/*` path still goes through the catch-all. Contract:
`deploy/contracts/meeting-bundle.v1`.
