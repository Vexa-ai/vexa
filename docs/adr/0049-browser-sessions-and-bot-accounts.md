# ADR 0049 — Browser sessions, bot accounts, and the Company profile

**Status:** proposed · 2026-10-13 · for v0.13.3 ([#1783](https://github.com/Vexa-ai/vexa/issues/1783)) ·
builds on ADR-0040 (one credential store) and the admin account (#1835, #1841) · defaults marked
*founder may strike* are open

## Context

A person wants to sign a bot in once — to Google, Microsoft or Zoom — and have every bot they send
afterwards join as that signed-in account. An organisation wants one such account for the company,
managed by its administrators. An administrator wants to work on company things (flows, policies,
templates, mail, friction, company bots) from their own account, without a second sign-in.

What already existed, checked in the tree on 2026-10-13:

- **A deployment-wide signed-in bot.** `BOT_AUTHENTICATED=true` makes every spawn restore one stored
  browser session from `BOT_USERDATA_S3_PATH` with a read-only key pair
  (`meeting_api/bot_spawn/auth_session.py`); the bot restores it with `aws s3` before launch
  (`bot/src/capture-bridge.ts` `launchBrowser`) and hands the rotated session back on clean teardown
  to `PUT /internal/browser-session/{session_uid}` (`meeting_api/session_profile/`, contract
  `session-profile.v1`). One identity for the whole deployment, provisioned by the operator with
  `make login` (`remote-browser/src/provision-cli.ts`): a headed browser, a human signs in over VNC,
  the profile is uploaded. A second concurrent spawn on the same stored session is a 409
  (`AuthSessionBusy`).
- **Signed-out detection.** The join layer recognises a signed-out profile
  (`auth_session_missing`, `modules/join/src/shared/admission.ts`, `googlemeet/join.ts`), the bot
  ends `failed` with that completion reason, and it is not retried (`lifecycle/retry.py`). Nobody is
  told: the reason lands in `meeting.data` only, and `get_bot_status` lists running bots only.
- **`browser_session` as a platform.** api.v1 and the gateway's `MeetingPlatform` name it; meeting-api
  excludes it from the bot cap and refuses to spawn it ("its 0.12 runtime path is not yet restored",
  #816). The 0.10 dashboard had a browser-session page (`services/dashboard`), deprecated.
- **A live view.** The bot image carries x11vnc, websockify and noVNC (join env base); the join
  module starts them on 5900/6080 for debugging (`shared/escalation.ts`, no password). Nothing routes
  to them: the gateway's only socket is `/ws`; meeting-api is the only control-plane service on the
  `bots` network.
- **One credential store.** The credential broker (ADR-0040) keeps credentials encrypted
  (AES-256-GCM locally, or OpenBao), behind role keys (`agent`, `human`, `git`), reachable only on the
  internal `connections` network.
- **The company layer has one writer.** The admin account (`VEXA_ADMIN_ACCOUNT`, `admin_account` on
  identity) is the only account that writes `_global`, and it has nothing else. Its home
  (`AdminHome`: Flows & mail, Templates, Friction, Workspace) shows only when someone signs in as it.
  The instance administrator (`is_admin`) reads `_global` and cannot write it, and the everyday
  account showed `_global` as a pinned tab beside Personal.

## Decision

1. **Browser sessions are a product feature.** A person opens a live browser from the chat (the
   `browser_session_open` tool) or from Connections. meeting-api spawns a bot-image workload in
   browser-session mode on the `bots` network; the browser opens the platform's sign-in page; the
   person sees and drives it in the page through noVNC, relayed terminal → gateway → meeting-api →
   workload, with the gateway's signed identity on every hop and no published port. VNC listens on
   the workload's loopback behind a per-session password; websockify is the only listener. When the
   page leaves the sign-in pages and the platform's auth cookies are present, the workload sends the
   profile to meeting-api and stops. The agent never types a password; the person signs in.
2. **A saved profile is a bot account.** `{id, owner (user:<id> | company), platform, display name,
   state ok | signed_out, verified_at}`. The profile (session-profile.v1 files) is a credential: it
   lives in the credential broker under a new role `bots`, held by meeting-api only, encrypted at rest
   like every other credential, never in a workspace, never in a log line. No second store.
3. **Bots come signed in.** `POST /bots` takes `bot_account` (an id, `default`, or `none`). The
   default is the caller's most recently verified account for the platform, then a company account
   the caller may use, then a guest bot as before. The bot restores the profile from meeting-api with
   its MeetingToken (`GET /internal/browser-session/{session_uid}`, session-profile.v1) instead of
   holding a store key, and writes the rotated session back to the same account. One live bot per
   account. A signed-out join flips the account to `signed_out`, and the meeting's data names the
   remedy (`browser_session_open`) so the agent can offer "sign in again" in one tap.
4. **The company bot** is a bot account owned by `company`. The admin account creates and deletes
   it. Who may use it is the company's policy `company_bot_use: all | admins | none`, default
   `admins` (*founder may strike*). Users never see its profile; they see its name and state.
5. **The Company profile.** An instance administrator's account menu offers *Switch to Company*. The
   terminal opens the admin account's session on that tab (a token minted named
   `company:<person id>`, twelve hours, counted only when identity answers `admin_account` for it)
   and keeps the person's own session beside it; *Switch back to <person>* restores it and revokes
   the company token. In Company mode the rail, chat and panel are the admin account's: AdminHome,
   `_global` pages, global flows, policies, templates, mail, friction and company bot accounts. The
   separate admin account keeps working. In an everyday account `_global` is no longer a tab or a
   navigator row.
6. **MCP first.** Tools `browser_session_open`, `bot_accounts_list`, `bot_account_verify`, and
   `bot_account` on `request_meeting_bot`. Dedicated UI only for the live browser view, the account
   menu switch, and a Bot accounts list in Connections.
7. **Audit.** Opening a session, saving, using (spawn), signing out and deleting an account are
   logged as typed events with account id, owner and platform, never with profile contents; the
   broker audits every store operation.

## Consequences

- meeting-api joins the `connections` network and holds the `bots` role key. A compromised
  meeting-api can read every bot account's profile — the same reach it had over the deployment-wide
  session's store, now per account and audited.
- The bot needs no store key for a bot account; the deployment-wide `BOT_AUTHENTICATED` mode stays
  as it was.
- Company-mode attribution is the token's name (`company:<person id>`) and the terminal's switch log;
  per-act attribution inside agent-api to the person behind a Company session is not built yet.
- `company_bot_use` is read by meeting-api from `VEXA_COMPANY_BOT_USE` until meeting-api reads
  `_global/POLICIES.md`.
- A platform's own challenges (2FA prompts, CAPTCHAs) are answered by the person in the live view.
