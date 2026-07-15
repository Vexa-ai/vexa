# ZAKI capture profile

This module is the fail-closed service boundary for a ZAKI-managed Minutes capture. It does not add
an HTTP route or deployment flag. `request_capture(...)` intersects four independently supplied
authorities before it touches the meeting repository or runtime:

1. the operator has enabled Minutes capture;
2. the tenant has enabled capture and recorded a versioned lawful-capture attestation;
3. the user requested this capture; and
4. quota permits it.

The grant is bound to one tenant, user, platform/native meeting identity, and—when supplied—an exact
SHA-256 of the validated meeting URL. It is valid for at most five minutes. The allowed path always
joins as **ZAKI Notetaker**, enables recording and transcription, stores only content-free policy
evidence under `meeting.data.zaki_capture`, and materializes immutable UTC audio/transcript/summary
expiries under `meeting.data.zaki_retention`. Callers cannot override the bot name or inject evidence.
Missing, malformed, expired, mismatched, or disabled authority returns a stable `CaptureDenial`
before repository/runtime mutation.

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
touch Redis, object storage, or recording JSONB. Late non-terminal bot callbacks cannot move the
meeting back from `stopping` to `active`; a genuine terminal callback is still accepted and retains
the capture attribution.

No public withdrawal route is introduced here. Hub/BFF routing, settings persistence, secrets,
charts, and activation belong to later slices.
