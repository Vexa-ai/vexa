# bot_accounts — saved signed-in browser profiles (ADR-0049)

A bot account is a saved signed-in browser profile (session-profile.v1 files) with metadata, owned
by one person (`user:<id>`) or the whole deployment (`company`). meeting-api keeps them in the
credential broker (credential-broker.v1 role `bots`, `POST /api/internal/bot-account`) and never on
its own disk.

A person signs an account in through a **browser session**: a `meeting-bot` workload in sign-in mode
(env `VEXA_BROWSER_SESSION`, no BOT_CONFIG) whose screen they see and drive in their page — noVNC,
relayed by meeting-api from the workload's port 6080. When the sign-in is confirmed the workload saves
the profile here and exits. A meeting bot then joins **as** the account: it restores the profile from
meeting-api before launch and writes it back on teardown.

| File | What it is |
|---|---|
| `broker_assertion.py` | the credential-broker.v1 assertion signer, vendored VERBATIM from `core/agent/contracts/credential-broker.v1/assertion.py` (`gate:fact-parity`, fact `credential-broker-assertion`). Never edit it here |
| `broker.py` | `BrokerClient` port · `HttpBrokerClient` (httpx; every request signed as role `bots`) · `FakeBroker` · `broker_from_env` |
| `sessions.py` | browser sessions and verify checks in meeting-api's Redis (`SessionStore` · `RedisSessionStore` · `InMemorySessionStore`); tokens, VNC passwords, the one-open-session hold and the reaper's index |
| `policy.py` | `VEXA_COMPANY_BOT_USE`, admin-api roles (`AdminOracle` · `HttpAdminOracle`, cached, fail closed) and `usable` |
| `relay.py` | the live view's two hops (`VncUpstream` · `HttpVncUpstream` — httpx + `websockets`, lazy) and `view_path_refusal` |
| `service.py` | `BotAccounts`: the ports and every rule the routes, the spawn and the lifecycle hook apply |
| `router.py` | the routes below |
| `feature.py` | the `VEXA_FEATURES` switch `browser_bots` (`BrowserBotsGate`, ASGI): off ⇒ every public `/bot-accounts…` route answers **403** `feature_disabled`, the socket closes 4403; `/internal/…` untouched. The parse is agent-api's (`core/agent/control_plane/features.py`), copied |

## Configuration

| Key | |
|---|---|
| `VEXA_BOT_ACCOUNTS_BROKER_URL` + `VEXA_BOT_ACCOUNTS_KEY_FILE` | capability `bot_accounts`. Either unset: every route answers **409** `bot accounts are not configured on this deployment`, and `POST /bots` spawns guest bots as before. An unusable key file refuses the boot |
| `VEXA_FEATURES` | the install's switched-on features. Empty (the release default) leaves `browser_bots` **off**: the public routes refuse `feature_disabled` and the MCP lists no bot-account tool. `all` or a list naming `browser_bots` serves them; `all,-browser_bots` takes it back. Saved accounts stay in the broker either way |
| `VEXA_COMPANY_BOT_USE` | who may **use** a company account: `all` · `admins` (default; the instance admins and the admin account) · `none`. The company's statement of this rule is `_global/POLICIES.md` `company_bot_use`; this env carries it to meeting-api until meeting-api reads policies itself. Whatever it says, only the **admin account** may create, sign in again or delete a company account |
| `VEXA_BOT_ACCOUNTS_SESSION_TIMEOUT_S` (900) · `VEXA_BOT_ACCOUNTS_CHECK_TIMEOUT_S` (180) | how long a session / check runs before the reaper (`BOT_ACCOUNTS_REAPER_INTERVAL_S`, 30) closes it and stops its workload; the workload's `maxLifetimeSec` is the backstop |
| `VEXA_BOT_ACCOUNTS_WORKLOAD_HOST` (`vexa-{workload_id}`) | the workload's name on the bots network (runtime.v1 `WorkloadStatus` carries no address) |

Roles come from admin-api `GET /internal/users/{id}/is-admin` (`ADMIN_API_URL` + `INTERNAL_API_SECRET`:
`is_admin`, `admin_account`), cached 30 s; any failure is neither role.

## Routes

Public (gateway, scopes `bot` · `tx`, rows in [`core/meetings/routes.v1.json`](../../../../../routes.v1.json)):

| Route | |
|---|---|
| `GET /bot-accounts` | `{accounts: [BotAccount + {usable, mine}], company_bot_use}` — the caller's and the company's |
| `POST /bot-accounts/sessions` | `{platform, display_name?, owner?: me\|company, account_id?}` → 201 `{session_id, status, platform, owner, view_path, ws_path, vnc_password, expires_at}`. One open session per person (409). `owner: company` or a company `account_id` needs the admin account (403). `account_id` = sign in again (same platform, 422) |
| `GET /bot-accounts/sessions/{id}` | `{session_id, status, platform, owner, account_id, detail, expires_at, vnc_password}` — `starting` → `ready` (workload running and its 6080 answers) → `saved` / `failed` / `closed` |
| `DELETE /bot-accounts/sessions/{id}` | stops the workload → `{session_id, status: "closed"}` |
| `GET /bot-accounts/sessions/{id}/view/{path}` | GET only; noVNC's own files only (`view_path_refusal`: no `..`, top-level pages or `app/ core/ vendor/ include/ utils/`), content-type passed through |
| `WS /bot-accounts/sessions/{id}/vnc` | binary relay to `ws://<host>:6080/websockify` (subprotocol `binary` when offered). The gateway relays it with the signed identity; `route_scopes` holds it to the `GET /bot-accounts/sessions/{id}` row |
| `POST /bot-accounts/{id}/verify` | → 202 `{account_id, check_id, status: "checking"}`; a verify-mode workload restores, checks and reports; `GET /bot-accounts` then shows `state` / `verified_at` |
| `DELETE /bot-accounts/{id}` | → `{account_id, deleted: true}`; a company account needs the admin account |

Only the session's owner may read, view or close it: anybody else gets 404 (WS close 4404).
`owner` in answers is the broker's form, `user:<id>` or `company`.

Internal (bots network, `include_in_schema=False`):

| Route | Admitted by | |
|---|---|---|
| `PUT /internal/bot-accounts/sessions/{id}/profile` | the session's token (random, stored as SHA-256, `hmac.compare_digest`) | session-profile.v1 `WritebackBody`, `parse_profile_upload`'s refusals (413 / 422) → broker `put` (a new id, or the session's `account_id`, owner and platform checked), state `ok`; the workload is stopped after the answer; log `bot_account_saved` (id, owner, platform — never contents) |
| `POST /internal/bot-accounts/checks/{id}` | the check's token | `{logged_in, detail}` → broker `state` `ok` / `signed_out` |
| `GET /internal/browser-session-check/{id}` | the check's token | the account's files, for the verify workload's restore |
| `GET /internal/browser-session/{session_uid}` | the MeetingToken, admitted for exactly that session | a meeting bot that joins as an account restores its profile (session-profile.v1 restore route): `meeting.data.bot_account_id` set, the session the newest one spawned on `bot-account:<id>`, the meeting live |

## The meeting bot

`POST /bots` `bot_account` (id · `default` · `none`) is resolved by `BotAccounts.resolve_for_spawn`
(see [`bot_spawn/README.md`](../bot_spawn/README.md)). The write-back of such a bot goes to its account
([`session_profile`](../session_profile/README.md)). When the lifecycle records `auth_session_missing`
on its meeting, `on_terminal` sets the account `signed_out`, logs `bot_account_signed_out`, and the
meeting's data gets `bot_account: {id, state: "signed_out", remedy: "browser_session_open"}`.

Tests: [`tests/test_bot_accounts.py`](../../../tests/test_bot_accounts.py).
