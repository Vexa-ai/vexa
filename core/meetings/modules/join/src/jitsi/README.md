# join/src/jitsi — Jitsi Meet join flow

Enter a Jitsi Meet room on whatever **deployment** the URL names (meet.jit.si is only the canonical
public one — the host is never rewritten; `buildJitsiMeetingUrl` appends hash-config overrides only:
receive-only mutes + the bot's display name). Admission and removal prefer the app's own runtime
verdict (`APP.conference.isJoined()`) over DOM heuristics. `join.ts`, `admission.ts`, `leave.ts`,
`removal.ts`, `selectors.ts`, `join.test.ts` (the URL-builder golden). Imports host symbols from
`../_host`, `playwright`, and Node builtins only.
