# dashboard_new — composition-root readiness findings (handoff for an autonomous session)

> Scope: the **composed Next.js app** under `clients/dashboard_new` (the wiring + screens + Next API
> routes), NOT the `@vexa/dash-*` bricks. The bricks are isolation-green; these are runtime-readiness
> gaps that only appear once the bricks are composed against a real backend.
>
> Each finding: severity · the platform principle it maps to · location(s) · scenario · expected vs
> actual · fix. A finding is "done" when the named fix lands **and** a `live*.spec.ts` (or a new L3
> test) proves the truthful behavior. Status legend: 🔴 open · 🟡 fix-ready · 🟢 fixed+verified.

## Evidence boundary (what was / was not proven)

- ✅ **Green (local):** `npm run build:bricks`, `npm run test:bricks`, and
  `VEXA_API_URL=http://127.0.0.1:8056 npm run build` all passed — the bricks build and their L2 suites pass.
- ⚠️ **Not run:** the `live*.spec.ts` real-stack Playwright specs — they require a running backend +
  Redis target and one spec spawns a real bot. **Every finding below lives in the gap those specs cover.**
  The next session's first move is to stand up a stack (compose on a host, or bbb) and run them.

The recurring root cause: the composition **reports intent/empties as if they were confirmed reality**
(P21 — "report state from evidence, not intent; this extends to the clients") and **swallows faults into
benign-looking states** (P18 — "fail loud and attributable"). The bricks are honest; the wiring isn't yet.

---

## DF1 — 🔴 Start-bot UI exposes only a narrow slice of `POST /bots` (capability gap)

- **Severity:** MEDIUM (functional parity — the user can't drive controls the backend + api-client support).
- **Principle:** parity / completeness (no P-violation; the seam is just under-surfaced).
- **Where:** `modules/join-form/src/types.ts:20` (`CreateBotRequest`), `modules/join-form/src/JoinForm.tsx:56`,
  `src/app/join/page.tsx:26` — the form submits only `platform`, `native_meeting_id`, `passcode`,
  `meeting_url`, `bot_name`.
- **Expected:** the form can drive the knobs the api-client already types — `language`, `task`,
  `recording_enabled`, `transcribe_enabled` (`modules/dash-api-client/src/ports.ts:39` `BotRequest`).
- **Actual:** those four are unreachable from the composed UI; `CreateBotRequest` omits them.
- **Fix:** extend `CreateBotRequest` + `JoinForm` (additive optional fields) to surface `language`, `task`,
  `recording_enabled`, `transcribe_enabled`; thread them through `join/page.tsx` into the `BotRequest`.
  Keep `platform` + `native_meeting_id` the only required fields. Verify a bot spawns with a non-default
  language/recording flag end-to-end.

## DF2 — 🔴 WS state reports `live` before the socket is proven; socket errors are invisible

- **Severity:** HIGH (truthful live state — the core P21 concern; the same class as the backend's
  "status:active before first frame").
- **Principle:** **P21** (state from evidence) + **P18** (fail loud — no error channel).
- **Where:**
  - `modules/dash-meeting-state/src/index.ts:218` — `setState({ connection: "live" })` runs **synchronously
    right after** `wsClient.start()` (`:217`), with no wait for open/auth.
  - `modules/dash-meeting-state/src/index.ts:211` — the `wsClientFactory({...})` call passes `onStatus`,
    `onTranscript`, `onChat` but **not `onError`**, though the client interface supports it.
  - `src/lib/browser-ws-transport.ts:24-26` — the native socket registers `message`/`open`/`close`
    listeners but **no `error` listener**, and `WsTransport` has no `onError` member at all.
- **Expected:** `connection` becomes `live` only on the transport's `open` (and a confirmed subscribe);
  an auth/handshake/socket failure flips to an `error`/`closed` state the UI can show.
- **Actual:** `live` is asserted on the *start command*; a failed socket shows as a silent non-update
  (or a bare `close`), so auth/socket failures are hard to see.
- **Fix:** (a) add `error` to the `WsTransport` port + an `addEventListener("error", …)` in the browser
  adapter; (b) thread `onError` through `dash-ws` → `createMeetingState`; (c) set `connection: "live"` on
  the transport `open`/subscribe-ack, not on `start()`; add a `connection: "error"` state. Verify with a
  `live*.spec.ts` that points at a bad/unauthorized `wsUrl` and asserts the UI shows an error, not "live".

## DF3 — 🔴 Config/auth failures degrade into empty or misleading UI (not a loud error)

- **Severity:** HIGH (misconfig & auth-failure are indistinguishable from "no data").
- **Principle:** **P18** (fail loud) + **P21** (don't render an unearned empty state).
- **Where:**
  - `src/app/providers.tsx:42` — `fetch("/api/config").then(r => r.json())` with **no `r.ok` check**; the
    JSON-500 error body from `src/app/api/config/route.ts:21-26` (returned when `VEXA_API_URL` is unset)
    is parsed *as if it were* `BrowserRuntimeConfig`, so `wsUrl`/`authToken` silently become `undefined`.
  - `src/app/api/vexa/[...path]/route.ts:104` — the `GET /meetings` branch returns `{ meetings: [] }` on
    upstream failure / fallback miss, and it returns **at line 104, above** the `if (!VEXA_API_KEY) → 401`
    gate at `:107`. So an unauthenticated or misconfigured `/meetings` call yields `200 {meetings:[]}`,
    never a 401 — misconfig looks exactly like "no meetings."
- **Expected:** a config fetch that isn't `ok` surfaces a visible "dashboard misconfigured" state; an
  unauthenticated `/meetings` returns 401 (and the UI shows "sign in / check config"); an upstream failure
  is distinguishable from a genuinely empty list.
- **Actual:** both collapse to a blank/"no meetings" UI.
- **Fix:** (a) in `providers.tsx`, check `r.ok` and route a non-ok config into a visible error state;
  (b) move the `!VEXA_API_KEY → 401` gate **above** the `/meetings` branch (or have the branch return an
  error envelope, e.g. `{ error, meetings: null }`, distinct from an empty list) so auth/upstream failure
  ≠ empty. Verify with a spec that runs with `VEXA_API_URL` unset and with a dead token.

## DF4 — 🔴 Recording errors are swallowed into "No recording yet"; `/master` is an unsealed read

- **Severity:** MEDIUM-HIGH (a 401/403/500/schema-drift on the recording read is shown as the normal
  no-recording state — hides real failure exactly where the user looks).
- **Principle:** **P18** (swallowed fault) + **P21** (misleading state); tail: **P4** (unsealed contract).
- **Where:**
  - `src/components/meeting-detail.tsx:85-93` — the `try { getTranscripts → getRecordingMaster }` `catch`
    is empty with the comment "no recording yet — the player shows 'No recording yet.'" Any error class
    (auth, server, schema drift) lands there.
  - `modules/dash-contracts/src/index.ts:226-232` — `RecordingMaster` is documented as **not** a sealed
    api.v1 component (`api.v1` seals `GET /recordings` + `GET /recordings/{id}` but not `/master`), so the
    player depends on an unsealed cross-process read (drift-prone).
- **Expected:** distinguish "no recording exists" (the empty case) from "the recording read failed"
  (auth/server/schema) — the latter is a loud, attributable error.
- **Actual:** all of them render "No recording yet."
- **Fix:** branch the catch on the error kind — a true "no recording" (e.g. 404/empty) → "No recording
  yet"; an auth/server/schema error → a visible error with the status. Separately, seal `/recordings/{id}/
  master` into api.v1 (lane:contract) or pin a golden so the dashboard's read can't drift silently
  (ties to the backend `gate:contract-conformance` work).

## DF5 — 🔴 VNC URL uses `bot_container_id` as the `/b/{token}` capability segment (unverified)

- **Severity:** MEDIUM (potential HIGH security — depends on whether `bot_container_id` is unguessable).
- **Principle:** **P20** (complete mediation — a capability token must be unguessable).
- **Where:** `src/components/meeting-detail.tsx:152-156` composes `/b/${meeting.bot_container_id}/vnc/...`,
  while the sealed schema documents `/b/{token}` as expecting an "unguessable capability token."
- **Expected:** the `{token}` segment is a capability token an unauthorized party can't guess/enumerate.
- **Actual:** `bot_container_id` is used directly; whether that id IS the intended unguessable capability
  (vs. a predictable name like `mtg-<meeting_id>-<short>`) is **not proven in dashboard_new**.
- **Fix:** verify what `/b/{token}` authorizes against on the backend. If `bot_container_id` is
  predictable, mint/serve a real per-bot capability token and use it here; if it genuinely is the
  capability, document + golden that binding so it's not accidentally weakened. This is an access-control
  question — resolve it before exposing VNC publicly.

---

## Suggested plan for the autonomous session

1. **Stand up a stack + run the untested specs** (compose on a host or bbb) so the `live*.spec.ts`
   real-stack lane runs — that lane is the proof surface for every finding here.
2. **Fix order (truthfulness first, the P18/P21 cluster):** DF3 (loud config/auth) → DF2 (truthful WS
   state + error path) → DF4 (recording error visibility) → DF1 (full spawn controls) → DF5 (verify the
   VNC capability binding; security-gate before any public exposure).
3. **Bank each as a regression** — a `live*.spec.ts` (or an L3 component test with a faulted transport/
   proxy) asserting the *truthful* behavior (error shown, not empty/"live"/"no recording").
4. **Cross-link to the platform work:** DF2/DF3/DF4 are the client face of P21/P18; DF4's `/master` tail
   and the unsealed read belong with the backend `gate:contract-conformance` follow-up.
