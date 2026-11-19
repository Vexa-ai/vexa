# sdk-join.v1

Private bot/runtime IPC. Start injects JWT and optional OBF/ZAK. Leave requests departure without destroying the session; ended confirms departure. Stop disposes the native runtime, with leave as a best-effort fallback. One start per process. Events never contain credentials or meeting identifiers. Only in_meeting proves admission. No capture operation belongs to this joining contract. The join module receives an injected port; it does not import this service transport.

Validate with `node core/meetings/contracts/sdk-join.v1/validate.mjs`; goldens contain inert credentials.
