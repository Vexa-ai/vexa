# deploy/dogfood/rig — the rehearsal rig (NOT a service, NOT for main)

This directory exists for one reason: **the code the product rehearsal runs on must have
provenance.** Until this commit it did not — 46 MCP tools, the whole in-conversation sign-in,
flows and workspace surfaces lived as a single file on one host, with no branch, no commit
and no backup. A rehearsal against that validates nothing you can ship, and one disk loses a
day of product decisions.

## What this is

`vexa_control_mcp.py` is a **prototype MCP server** built on 2026-08-30 on the storm rig,
hardened by pointing cold-start agents at it and fixing everything they hit. It went wide
where the real service is deliberately narrow: sign-in inside the conversation, the
`whats_waiting` protocol entry point, flows authoring and durable scheduling, workspace and
the propose/validate knowledge lifecycle, composed deep-links into the terminal.

**It is not the product and it must never become one.** The product MCP service is
`core/meetings/services/mcp/` — deployed, gated, tested, tools as thin FastAPI routes. This
prototype's job is to be the stage the four-act rehearsal runs on, and then to be read,
ported and deleted. Two of its findings have already been adopted upstream: the
`asked_by_a_human` gate on speaking (taken FROM the real service, not invented here) and the
operating doctrine now in `VEXA_INSTRUCTIONS`.

## Why this branch never merges

`mcp-rehearsal-rig` is a provenance branch. Merging it would create the second server the
founder explicitly refused. What merges is the PORT — slice by slice, into the real service,
each slice carrying only what the rehearsal proved worth carrying.

## Known, and deliberate, differences from anything shippable

- **`VEXA_RIG_MODE`** (default on here) enables a `token=` argument fallback and a
  `GET /do/<tool>` bridge. Both put a credential in a query string: right for a fetch-only
  agent on a private host, wrong anywhere requests are logged. `VEXA_RIG_MODE=0` disables both.
- **Identity proves mailbox control and nothing more.** Every door — `/login`, the
  `start_onboarding`/`confirm_login` tools and the OAuth consent screen — mails a 6-digit code and
  issues nothing until it comes back, and creates an account only for an address the instance's
  sign-in admission admits. A code signs in only the address it was mailed to; `/login` binds the
  address at its second step and reads it from there. Federation is the upgrade an organisation
  will require.
- **Every sign-in door is off unless switched on.** `VEXA_RIG_OAUTH_ENABLED=1` (via
  `rig.sh restart`) opens the OAuth surface, `/login` (with `/login/claim` and `/start`) and the
  `start_onboarding`/`confirm_login`/`auth_link`/`auth_claim` tools. Off, those paths answer 404,
  the tools refuse, and the OAuth tokens this server issued stop resolving.
- **Sign-in codes are budgeted three ways.** Per address, cumulatively across every code and door
  (`VEXA_RIG_CODE_ADDRESS_CAP` codes and `VEXA_RIG_CODE_ADDRESS_FAILS` wrong tries per
  `VEXA_RIG_CODE_ADDRESS_WINDOW_S`; asking again never resets a count); per caller
  (`VEXA_RIG_CODE_SOURCE_BUDGET`); and process-wide (`VEXA_RIG_CODE_BUDGET`). The caller is the TCP
  peer, or, behind a proxy, the header `VEXA_RIG_CLIENT_ADDRESS_HEADER` names; without it a proxied
  rig cannot tell callers apart and only the other two caps apply.
- **OAuth clients redirect only where a person can see.** Registration takes https redirect URIs,
  or http to loopback; the consent screen names the host the code is sent to, and redirects only to
  a URI the client registered, exactly. Refresh tokens expire after 30 days, rotate on every use,
  stay with the client they were issued to, and re-ask sign-in admission.
- **Git-backed workspace verbs go through the gateway as the person.** `workspace_attach`,
  `workspace_push`, `workspace_pull`, `workspace_import`, the deploy key and the git-remote status
  reach agent-api at the path the agent manifest's `forward` maps, with the person's own key, so a
  broker-backed git store can act for them. A delegated worker keeps the internal tier with its
  regime and ceiling and never borrows that key; against a broker-backed store, agent-api refuses it.
- **`workspace_write` is a dev double** — agent-api exposes no HTTP write, so this reaches the
  volume directly. That missing endpoint is the real gap behind first-class remote workspaces.
- **Mail is a double** (mailpit): nothing leaves the host. It holds every message sent to anyone,
  sign-in codes included, so `mail_inbox`/`mail_read` are account-scoped: a caller sees only
  messages addressed to their own address, and nothing when that address is unknown. A
  delegated worker is refused, even for the person it acts for.

## Sign-in for external MCP clients

### What it is, and who it is for

Four verbs let an MCP client that somebody connected **with no account** sign its person in from
inside the conversation: `auth_link` + `auth_claim` (a page with a one-click-and-a-code approval)
and `start_onboarding` + `confirm_login` (a 6-digit code relayed through the chat). The client
then registers the token on its connection (header, or `?c=<token>`) and reconnects.

**They are for external clients only.** A hosted Vexa session (a chat turn, a scheduled routine,
a post-meeting run) is dispatched with a delegation token (`vxd_`) that already names its person,
and no new sign-in can change the regime that delegation carries. So:

- A delegated caller that calls any of the four is refused with
  `{"refused": "already_signed_in", ...}`: the session is already signed in as this person; this
  is not a sign-in problem; a tool that answered `human_session_required` needs the person present
  in a chat turn they sent, so they can ask for it in chat. It is told not to retry and not to file
  friction about it.
- A delegated caller is not offered the four in `tools/list`. The worker's allow-set admits the
  whole server prefix (`core/agent/worker/mcp_tools.v1.json`), so the refusal still stands behind
  the listing for a worker that names one anyway.
- The product MCP does not serve these verbs at all (`RIG_ONLY_UNNAMED_IN_CORE` in
  `core/agent/tests/test_prompt_tools_served.py`).

**Limits.** A handle from `auth_link` and a code from `start_onboarding` last **15 minutes of wall
time** (`LOGIN_TTL`) and work **once**. The handle is kept in the rig's sealed store
(`oauth/logins`), so it survives across turns and restarts, but not past its 15 minutes. A code
allows 5 wrong attempts. Every verb is off unless `VEXA_RIG_OAUTH_ENABLED=1`.

**Every link is built on the public address**, `VEXA_PUBLIC_MCP_URL`: the sign-in page, the
`persist_now` connect command and the skill URL after sign-in. The verbs refuse to hand out a link
when that address is unusable.

**Failure modes the caller sees.**

| answer | cause | what to do |
|---|---|---|
| `refused: already_signed_in` | a hosted session called a sign-in verb | nothing; tell the person they are signed in, and ask in chat for anything needing them present |
| `sign-in links are unavailable from this server` | `VEXA_PUBLIC_MCP_URL` is missing, private, link-local, a single-label name, not https (loopback is allowed for a laptop rig), or not ending in `/mcp` | operator sets the public address; the agent reports friction once |
| `sign-in through this server is switched off` | `VEXA_RIG_OAUTH_ENABLED` is not `1` | sign in on the web instead |
| `unknown, used, or expired code` | the handle was redeemed, or its 15 minutes passed | `auth_link()` mints a fresh one |

### How it works

1. `_Auth` authenticates the request. A verified `vxd_` delegation sets `CALL_DELEGATED`/`CALL_SCOPE`
   (the one place identity is decided); an anonymous caller sets neither.
2. `tools/list` is answered from `mcp.list_tools`, which the rig wraps
   (`_list_tools_for_caller`): a delegated caller's list omits `EXTERNAL_SIGNIN_VERBS`.
3. Each of the four verbs first calls `_signin_refusal(verb)`: delegated → `already_signed_in`;
   switch off → `SIGNIN_OFF_JSON`; public address unusable (`public_origin.problem(CANONICAL,
   allow_loopback=True)`) → `sign-in links are unavailable`, logged to stdout with the reason.
4. Otherwise `auth_link` writes a handle with `exp = now + LOGIN_TTL` to `oauth/logins` and returns
   `<public origin>/login?h=<handle>`; `auth_claim` reads it, returns the token once, and deletes it.

**Single writer.** `oauth/logins` is written only by `auth_link`, `auth_claim` and the `/login`
page in this module. `VEXA_PUBLIC_MCP_URL` is written once, at process start: by `rig.sh`, or by
`deploy/dogfood/minutes-stack/agent_mcp.py` from its lock's `public_url`. The rule for what
counts as public lives in one file, [`public_origin.py`](public_origin.py), read by both.

**Configuration.** `VEXA_PUBLIC_MCP_URL` (table below); on Minutes, `public_url` in the agent-mcp
configuration, which refuses to boot without a usable one
([`../minutes-stack/README.md`](../minutes-stack/README.md#agent-mcp-service)). The listen address
travels separately as `VEXA_MCP_LISTEN_HOST`, admitted by the transport's host guard and never
published.

### Why it complies

Architecture (`docs/docs/governance/architecture.mdx`):

- **P18 fail loud.** An unusable public address stops the Minutes boot with the reason, and the rig
  refuses a link rather than handing out one that opens nowhere. Ungated: reviewed; pinned by
  `test_a_private_public_url_refuses_boot` and
  `test_auth_link_never_hands_out_a_link_on_a_private_address`.
- **P14 config is validated, delivered by env.** The public address is an explicit lock input,
  validated before anything else loads (`test_main_refuses_before_reading_anything_else`).
  Ungated: reviewed.
- **P23 one writer, no re-derivation.** The public address is written once at start and no longer
  re-derived from the listen host; the persist command reads `CANONICAL` rather than re-reading the
  environment with its own default. Ungated: reviewed.
- **No duplication.** One rule, `public_origin.problem`, serves both the boot check and the rig's
  check. `gate:python` runs both test suites.

Security:

- **Who can call.** Anyone may call the four verbs anonymously; that is their purpose and it is
  unchanged (`test_an_anonymous_external_client_still_gets_a_link_on_the_public_address`,
  `test_an_anonymous_client_can_still_start_onboarding`). A delegated worker may call none of them.
- **Enforcement per hop.** `_Auth` verifies the delegation; the listing omits the verbs; each verb
  refuses again before any store write, mail send or admin-api call. Nothing is minted, mailed or
  spent for a refused worker, and a refused `auth_claim` leaves the person's pending handle intact.
- **Deny tests.** `test_a_delegated_worker_is_refused_every_external_signin_verb` (one per verb),
  `test_a_delegated_worker_cannot_claim_a_pending_handle`,
  `test_a_worker_is_refused_even_while_signin_is_switched_off`,
  `test_a_worker_is_not_offered_the_signin_verbs_but_an_external_client_is`,
  `test_auth_link_never_hands_out_a_link_on_a_private_address`, `test_public_origin_refuses`
  (all in [`tests/test_signin_external_only.py`](tests/test_signin_external_only.py)), and
  `test_a_private_public_url_refuses_boot`,
  `test_a_loopback_or_plain_http_public_url_refuses_boot`
  ([`../minutes-stack/test_public_url.py`](../minutes-stack/test_public_url.py)).

## Running it

`rig.sh status|config|up|restart|down` supervises the pieces; `flows-up.sh` brings up the flows
API and worker as processes so edits are live on restart. Both expect the dogfood stack from
`deploy/dogfood/` to be running.

**The server runs out of its own venv, `./.venv`, built by `rig.sh up` from
[`pyproject.toml`](pyproject.toml).** It used to run out of `$VEXA_FLOWS_SRC`'s venv — a stale,
shared checkout's — and on 2026-09-03 that cost 2.5 minutes of downtime when a module the import
chain reached was not installed there, and a fix that meant installing a package into a venv three
other things also use. The flows API and worker still use the flows venv; they are that checkout's
processes. The control server is not.

**`rig.sh down` stops the server by pidfile** (`$HOME/.storm/run/control-mcp.pid`, or
`VEXA_RIG_RUN_DIR`), never by `pkill -f`. The pattern matched stage-1's own ssh session on the same
day, so stopping the rig killed the session doing the stopping.

### What it has to be told

These, all optional, each defaulted to what the bbb host has always used — so an
unconfigured rig starts exactly as before, and a rig anywhere else is four exports rather than an
edit to the source. `rig.sh config` prints what the current environment resolves to.

| variable | what it names | default |
|---|---|---|
| `VEXA_FLOWS_SRC` | the flows checkout's `core/flows`: the flows API, the worker, their venv, and the engine `fact_emit` imports. **Not the control server's interpreter any more** | `/home/dima/dev/wt-line/core/flows` |
| `VEXA_RIG_VENV` | the control server's own venv, built from `pyproject.toml` on `up` | `./.venv` beside this README |
| `VEXA_RIG_RUN_DIR` | where the pidfile `down` stops by is written | `$HOME/.storm/run` |
| `VEXA_PUBLIC_MCP_URL` | the name the server PUBLISHES — sign-in links, the `/connect` bootstrap, and the transport's host guard at once. Must be https and a public name (loopback is accepted for a rig on a laptop); otherwise the sign-in verbs refuse to hand out a link | `https://rig.dev.vexa.ai/mcp` |
| `VEXA_MCP_LISTEN_HOST` | an extra `host:port` the transport's host guard admits — the in-network address workers reach the server at. Admitted, never published | unset |
| `VEXA_UI_URL` | the terminal `deeplink()` sends people to. **Not** where an invite link comes from: agent-api composes that one (`workspace_membership.invite_link`, on its own `VEXA_UI_URL`) and `workspace_invite` hands back what the route returned — this server used to build `<VEXA_PUBLIC_MCP_URL>/join?i=…`, its own address, and every link it gave out went nowhere (Vexa-ai/vexa#1635) | `https://app.dev.vexa.ai` |
| `VEXA_MCP_DELEGATION_SECRET` | the HMAC key `vxd_` delegation tokens are verified against; read from `$HOME/.storm/delegation-secret` and never echoed | unset → every delegated token is refused, none admitted unverified |

The same block with its reasoning is in [`../env.dogfood.example`](../env.dogfood.example).

`VEXA_FLOWS_SRC` is the only one the SERVER reads for itself, and it reads it for exactly one
tool: `fact_emit` imports the flows engine in-process; every other flows surface goes over HTTP.
When the tree is not on the host the server still starts, every other tool is unaffected, and
`fact_emit` answers `{"unavailable": "fact_emit", ...}` naming the variable to set — rather than
raising an ImportError an agent will read as "Vexa is broken".

Nothing here names a home directory any more. The server `rig.sh` starts is the file sitting next
to it: the repo copy when run from the repo, and the `~/.storm` symlink to that same file when run
from there.

## The rig interpreter needs PyYAML

The rehearsal package (`deploy/dogfood/rehearse/`) reads `states.yaml` with PyYAML. The control MCP imports it lazily on the first `rehearse`/`subject_reset` call, so a venv without `pyyaml` fails only then, as `ModuleNotFoundError: yaml` inside `UnexpectedToolError: Error executing tool rehearse` (seen 2026-09-06 after the lane collapse). Install into the rig's venv: `<venv>/bin/python -m pip install pyyaml`.
