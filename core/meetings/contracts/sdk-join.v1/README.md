# sdk-join.v1

Private parent/worker IPC for native joining. Start injects ephemeral JWT and optional OBF/ZAK; stop requests leave and cleanup. Events contain no credentials or meeting identifiers. Only in_meeting proves admission. Authentication, connection and waiting states do not. Failures terminate the worker. One start per process; duplicate start is a protocol error. No audio or recording operation belongs to this contract.

Validate: `node core/meetings/contracts/sdk-join.v1/validate.mjs`. Goldens use inert credentials.
