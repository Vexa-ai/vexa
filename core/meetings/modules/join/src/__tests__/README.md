# join module — test suite

Tests use the module's pass/throw style (`npx tsx <file>.test.ts`).

- `shared/selector-validity.test.ts` — selector validity checks
- `googlemeet/admission.test.ts` — Google Meet admission-outcome detector fixtures
- `googlemeet/session.test.ts` — authenticated-session guard fixtures
- `googlemeet/leave.test.ts` — Google Meet leave-flow fixtures
- `googlemeet/english-pin.test.ts` — the English pin: Meet's English waiting room and denial read over fixture DOMs; German control reports `ui_locale_mismatch`
- `shared/ui-locale.test.ts` — the UI-locale check on every platform and the `UiLocaleMismatchError` typing of a failed join
- `googlemeet/humanized/humanized.test.ts` — humanized input interaction tests
- `msteams/modals.test.ts` — Teams modal dialog tests
- `msteams/leave.test.ts` — Teams leave-flow fixtures
- `zoom/join.test.ts` — Zoom URL and join-flow fixtures
- `jitsi/join.test.ts` — Jitsi URL and join-flow fixtures
- `jitsi/password.test.ts` — Jitsi password prompt tests
- `jitsi/admission.test.ts` — Jitsi admission and lobby fixtures
- `defaultBotName.test.ts` — default-bot-name env reading and joinMeeting wiring
