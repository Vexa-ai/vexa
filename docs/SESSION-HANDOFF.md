# Session handoff — 2026-06-25 (terminal meeting-canvas + auth audit)

A compressed record of a long working session so context survives a restart. Git holds the
per-commit detail; this is the *state, decisions, and open threads*.

## Where things stand (HEAD `3bdfafa9`, on `0.12`, deployed to BBB)

### Shipped this session — the Meeting Canvas became a real generative-UI app
The meeting page is now rendered by a **harnessed, agent-authorable view** (`views/meeting.tsx` in the
workspace), not hardcoded React. The engine lives in `clients/terminal/src/canvas/`:
- **Runtime** (`runtime.tsx`): validate (acorn allowlist) → transpile (sucrase) → execute in an injected
  scope → error boundary with **last-good fallback** + a trial-render go-live gate. A bad view can't ship.
- **Validator** (`validator.ts`): the harness — blocks imports/`fetch`/`eval`/DOM/raw `style`. The cage that
  makes non-tech-authored views safe and consistent.
- **Kit** (`kit.tsx`): self-formatting `ui.*` (empty/loading/overflow handled per component) + `ui.EntityList`
  (the `Scheduled ▾`-style entity pills: dot + name + context + hover card + research + ▾ options) +
  `ui.LiveTranscript` (horizontal live caption band).
- **Declarative consumption hooks** (`hooks.ts`/`useMeeting.ts`): `useMeeting`/`useTranscript`/`useSpeakers`/
  `useEntities`/`useSignals`/`useMeetingDocs` — the ONLY data access (no raw streams).
- **Actions** (`actions.ts`): `research`/`openDoc`/`copyRef` + the meeting copilot effects.
- **Mock + Eval bench** (`mockSource.ts`, `EvalPanel.tsx`): never-empty fallback + a controllable synthetic
  data bench (scenario/playback/inject) so views feel alive and are demoable with no live meeting.
- **Default view** = a **sales-call cockpit**: top strip (Live/title/elapsed + Brief + Report-when-ready) →
  People / Companies / Numbers / Signals entity pills → horizontal live transcript at the bottom.

Other terminal work this session: right-rail single chat with Cursor-style header (session switcher + new
chat) + stop button + auto-resize/file-and-screenshot attachments (`/api/workspace/upload`); preview/pinned
tabs everywhere; tab-header middle-click-close + copy-reference; `/routine` chat command; routine
enable/disable (file is source of truth); markdown tables; transcript timestamps as local time; distinct
`agent-worker` image; **workspace is a free zone now — governance is prompt-only** (no more
"workspace.v1 violation — reverted"); subject unified to `u_live` everywhere.

### Bugs fixed late in the session (the data was right; the frontend was stale)
- Entity-cards (people/companies/numbers) now route into their groups + dedup; **Signals holds only signals**.
- A bound meeting shows **live data, never the mock** (mock only for the unbound canvas or explicit toggle).
- **Meeting binding**: `TabHost` now subscribes to `onDidParametersChange` so the shared preview tab
  re-binds on param swap; `key={meetingId}` remount; resolve **real meetings first** (mock never shadows a
  real id). Isolation test PROVED the backend returns correct per-meeting transcripts — the wrong-data was a
  stale dev bundle + the preview not re-binding.
- `server.mjs` hardened so a transient backend `ECONNRESET` (the `/ws` proxy) no longer crashes the dev server.

## Architecture decisions confirmed (agreed, not yet built)
- **Objects are peer surfaces** registered via `registerList` + `registerTab` (see `surfaces/*.tsx`):
  Meetings · Files · Routines — and next, **Dashboards**.
- **Dashboards = the same canvas engine, scheduled instead of live.** A dashboard is a folder:
  `dashboards/<name>/{dashboard.json (fields-schema + routine binding), data.json (values), view.tsx}`.
  First-load shows the **fields + the updating routine** (self-describing) before data arrives.
- **Routines update dashboards.** Routines gain an **`exec` flavor** (run pure code) alongside the existing
  `agent` flavor. **Integrations** = connector code the exec-routine runs to pull analytics/email/calendar
  into the workspace (the free zone); the dashboard view reads it. Needs a **secrets mechanism for workers**.
- Build order: (1) Dashboards-as-canvas object (pure reuse) → (2) routine `exec` flavor + first integration →
  (3) secrets + more connectors.

## AUTH (§0) — audited this session; the big finding
**A well-designed auth system already exists — the terminal just doesn't use it.**
- Guarded path is solid: `core/identity` (sealed `identity.v1`: scoped tokens + `canAccess` owner-only
  authZ), `admin-api` (user/token CRUD + `/internal/validate` oracle, fail-closed HMAC), `gateway` (validates
  `x-api-key`, enforces scopes, injects `x-user-id`), `dashboard` (real Google OAuth + magic-link → mints a
  token → calls the gateway).
- **Hole:** the **terminal has no auth** (hardcoded `subject`, plain query param, no token/login) and
  **agent-api trusts the client-supplied `subject` with zero verification** — no token check, no scope check,
  and the `canAccess` port is **never wired**. agent-api is a second, unauthenticated front door (the terminal
  hits it directly, not via the gateway). Anyone can pass `subject=<any user>` and read/act as them.
- Two concrete bugs: dashboard `JWT_SECRET` falls back to `"default-secret-change-me"`; invocations carry
  `subject` unsigned (the per-dispatch signed token, ADR-0003, is deferred).
- **Recommended §0 (reuse, don't rebuild):** (1) front the terminal's agent-api calls **through the gateway**
  so it authenticates + injects `x-user-id`; (2) terminal login mirroring the dashboard (OAuth/magic-link →
  token → cookie → `Authorization: Bearer`); (3) derive subject from the verified token, **reject
  client-supplied subject**; (4) wire `canAccess` into workspace/sessions/routines (404 on deny); (5) fix the
  `JWT_SECRET` fallback; later, the signed per-dispatch invocation.

## Open threads / next steps
1. **§0 auth** as above (highest priority for multi-user / production).
2. **Verify meeting binding** end-to-end after a fresh terminal start (stale bundle was masking the fix).
3. **Dashboards scaffold** (build-step 1).
4. **Tighten `agents/meeting.md`** so People surfaces prospects/stakeholders, not every name mentioned.
5. **Prod build on BBB** (`next build && start`) to kill the ~30s dev cold-load (server responds in ~58ms;
   the lag is the unminified dev bundle).
6. `agents/meeting-ui.md` (the seed manifest the agent reads when vibecoding views) should be synced with the
   in-code `manifest.ts` so the agent knows the new primitives (`ui.EntityList`, `useSignals`, etc.).

## Ops note (see LEARNINGS.md)
The terminal dev server must be launched from a **persistent shell** (Cursor integrated terminal / a task),
NOT one-shot SSH `setsid`/`nohup` — those don't survive on BBB. To start it:
`cd /home/dima/vexa-0.12/clients/terminal && PORT=3008 node server.mjs`  (first compile ~1–2 min; `.next` may
need a fresh build).
