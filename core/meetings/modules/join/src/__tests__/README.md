# join module — test suite

Tests use the module's pass/throw style (`npx tsx <file>.test.ts`).

- `shared/selector-validity.test.ts` — selector validity checks
- `googlemeet/admission.test.ts` — Google Meet admission-outcome detector fixtures
- `googlemeet/session.test.ts` — authenticated-session guard fixtures
- `googlemeet/leave.test.ts` — Google Meet leave-flow fixtures
- `googlemeet/humanized/humanized.test.ts` — humanized input interaction tests
- `msteams/modals.test.ts` — Teams modal dialog tests
- `msteams/leave.test.ts` — Teams leave-flow fixtures
- `zoom/join.test.ts` — Zoom URL and join-flow fixtures
- `jitsi/join.test.ts` — Jitsi URL and join-flow fixtures
- `jitsi/password.test.ts` — Jitsi password prompt tests
- `jitsi/admission.test.ts` — Jitsi admission and lobby fixtures
- `defaultBotName.test.ts` — default-bot-name env reading and joinMeeting wiring
