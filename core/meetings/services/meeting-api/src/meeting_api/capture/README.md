# ZAKI capture profile

This module is the fail-closed service boundary for a ZAKI-managed Minutes capture. It does not add
an HTTP route or deployment flag. `request_capture(...)` intersects four independently supplied
authorities before it touches the meeting repository or runtime:

1. the operator has enabled Minutes capture;
2. the tenant has enabled capture and recorded a versioned lawful-capture attestation;
3. the user requested this capture; and
4. quota permits it.

The allowed path always joins as **ZAKI Notetaker**, enables recording and transcription, and stores
only content-free policy evidence under `meeting.data.zaki_capture`. Callers cannot override the bot
name or inject arbitrary evidence. Missing, malformed, or disabled authority returns a stable
`CaptureDenial` before repository/runtime mutation.

This is the first S03 tracer. Consent withdrawal and its terminal lifecycle state build on this
front door; public Hub/BFF routing, settings persistence, secrets, charts, and activation belong to
later slices.
