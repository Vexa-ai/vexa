# event.v1 — an external event into the control plane — UNSEALED (stub)

The event-source → agent-api ingress seam. Email (`email.received`), the post-meeting reconnect
(`meeting.completed`), task transitions, news — all ride this ONE envelope. The ingress (`POST /events`)
maps an Event to a `unit.v1` Invocation (trigger `event`, or `transcription` for `meeting.completed`)
and the SAME Dispatcher fans it out — so a new event source is a new `name`, not a new code path
(the "one fan-in" thesis). The Event carries WHO it concerns (`subject` = the person), an **opaque**
`source` ref a cred-gated tool resolves (never the bytes — email/calendar stay tools+event-sources,
not platform domains), and optionally the `plan` the fired unit runs (else the ingress applies the
default plan registered for the event `name`, e.g. Inbox-triage for `email.received`).

**Status: UNSEALED** (stub) — sealed in the MVP3 event/tool MVP. `gate:schema` validates its goldens
(`Event.email`, `Event.meeting`) against `#/$defs/Event`.
