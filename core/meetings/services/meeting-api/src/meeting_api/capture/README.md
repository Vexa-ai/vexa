# ZAKI capture profile

This module is the fail-closed service boundary for a ZAKI-managed Minutes capture. It does not add
an HTTP route or deployment flag. `request_capture(...)` intersects four independently supplied
authorities before it touches the meeting repository or runtime:

1. the operator has enabled Minutes capture;
2. the tenant has enabled capture and recorded a versioned lawful-capture attestation;
3. the user requested this capture; and
4. quota permits it.

The grant is bound to one tenant, user, platform/native meeting identity, and—when supplied—an exact
SHA-256 of the validated meeting URL. It is valid for at most five minutes and single-use: the
meeting row stores only a SHA-256 of its opaque grant id, and the atomic spawn transaction rejects
any replay even after withdrawal moves the first row out of the active set. Spawn and withdrawal use
the same per-user transaction lock; the latest scope withdrawal is a monotonic tombstone that also
rejects a different grant unless its authorization is strictly newer than the withdrawal. The allowed
path always joins as **ZAKI Notetaker**, enables recording and transcription, stores only content-free
policy evidence under `meeting.data.zaki_capture`, and materializes immutable UTC
audio/transcript/summary expiries under `meeting.data.zaki_retention`. Explicit meeting URLs must use
the approved host for their declared platform. Callers cannot override the bot name or inject
evidence. Missing, malformed, expired, mismatched, or disabled authority returns a stable
`CaptureDenial` before repository/runtime mutation.

The runtime kernel still rechecks owner quota. If that defense-in-depth check rejects after the
meeting row was reserved, the row is made terminal (`failed`) and its capture evidence becomes the
named `quota_exhausted` non-capture state; it never remains an active `requested` orphan.

`withdraw_capture(...)` is the second S03 tracer. It is tenant/user/meeting scoped and takes the
exclusive meeting-write barrier before storing `zaki_capture.state=withdrawn`, the original UTC
withdrawal instant, `withdrawal_reason=consent_withdrawn`, and `stop_requested=true`. Only after that
commit does it publish the bot leave command. A booting workload is also torn down directly because
it may not yet be listening for Redis commands. Repeated withdrawal preserves the first timestamp
and safely retries the leave command.

Recording and transcript writers take the shared side of the same cross-process barrier and refuse
entry once withdrawal is durable. A write already holding the lease drains; a later write cannot
touch Redis, PostgreSQL, object storage, or recording JSONB. Buffered Redis transcript data is
purged when its durable flush observes withdrawal rather than being retained for retry. Late
non-terminal bot callbacks cannot move the meeting back from `stopping` to `active` or enter the
durable audit trail; a genuine terminal callback is still accepted and retains the capture
attribution.

No public withdrawal route is introduced here. Hub/BFF routing, settings persistence, secrets,
charts, and activation belong to later slices.
