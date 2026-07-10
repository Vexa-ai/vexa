# jitsi — the Jitsi Meet join flow

_join · platform folder · one concern: drive a browser into a Jitsi Meet room and
report the admission verdict._

Jitsi Meet is **self-hostable** — meet.jit.si is only the canonical public
deployment — so this flow never rewrites the host: the URL the embedder hands in
IS the deployment joined (`buildJitsiMeetingUrl` only appends hash-config
overrides: receive-only mutes + the bot's display name). The admission and
removal oracles prefer the app's own runtime API (`APP.conference.isJoined()`,
stable across stock deployments) over DOM heuristics; selectors carry
newest-UI-first fallbacks for builds that strip the global.

- `join.ts` — `buildJitsiMeetingUrl` (hash-config, embedder overrides win) + `joinJitsiMeeting` (prejoin name entry, prejoin-disabled path, password prompt).
- `admission.ts` — `waitForJitsiMeetingAdmission` (lobby "knocking" wait, escalation) + `checkForJitsiAdmissionSilent`.
- `leave.ts` — `leaveJitsiMeeting` (`APP.conference.hangup()` first, hangup-button + menu fallback, forced-navigation last resort).
- `removal.ts` — `startJitsiRemovalMonitor` (isJoined() debounce, kick/termination texts, origin-change fast path).
- `selectors.ts` — constants only; gated by `../shared/selector-validity.test.ts`.
- `join.test.ts` — the URL-builder golden (L2, no browser): `npx tsx src/jitsi/join.test.ts`.

Proof level: L1/L2 offline (URL golden + selector validity). The live join flow
awaits its L4 run against a real room (same obligation as every platform, P19).
