# agent · control_plane · routers

The 124 HTTP routes agent-api serves, one module per **owner**. `api.py`'s `create_app` was 2,868
lines and held all of them (78 then), which is why every lane that touched agent-api touched one file — and
why the identity boundary was hard to see (seam backlog **B3**).

Each module is one function, `build(**deps) -> APIRouter`. `create_app` builds what the routes are
built out of and hands it over; each `build()` declares which of it that router takes, so *"what
does this router depend on"* is answerable by reading one line.

**Handler bodies moved byte for byte.** They closed over `create_app`'s locals, so `build()`
rebinds each dependency to **the name it already had** — not one identifier inside a handler
changed. `git diff -M` reads this as a move, and a reviewer is reading the code they reviewed
before.

| module | routes | what it owns |
|---|---|---|
| [`health.py`](health.py) | 3 | `/health`, `/api/version` and `/.well-known/mcp-tools.json` — the answers that must work when nothing else does. |
| [`ingress.py`](ingress.py) | 2 | The two internal doors a dispatch arrives through: `/invocations` (the internal tier, or a routine job agent-api signed) and `/events` (the internal tier's event.v1 ingress). Both authenticate the caller before reading a body that names a person. |
| [`chats.py`](chats.py) | 11 | The conversation surface: the `/api/chat` SSE turn and `/api/chat/submit`, the chat's pending inbox, target, reset, names and order, and `/api/sessions*`. No delegated identity starts a turn here. |
| [`routines.py`](routines.py) | 5 | `/api/routines*` — the clock that wakes agents: a routine compiles to a schedule.v1 job that comes back through `/invocations`. Arming one needs a person in the loop. |
| [`admin.py`](admin.py) | 6 | The operator's surface: `/api/admin/*` (the hidden panel), `/api/global/ready` (the organisation tier), `/api/models`, and the two credential self-tests. Internal-tier gated; not a user surface. |
| [`meetings.py`](meetings.py) | 7 | The meeting seam: relay health, where a meeting's report lives, the Highlight scan (`transcript_terms`) and the annotation layer it writes and the transcript canvas reads, and the live transcript stream a chat renders beside the conversation. |
| [`scaffolds.py`](scaffolds.py) | 8 | One record per arrival (PRD §5.5): mint, read, redeem the transcript share, the hand link and `/internal/has-history` — plus the two reads a panel does around it, `/api/links/resolve` and `/api/desk/touch`. |
| [`friction.py`](friction.py) | 1 | `POST /api/friction`, the rough-edges ledger's door on agent-api (PRD decision 33): an HTTP client of flows' `/friction`, for the worker and the terminal, which cannot reach flows. **Kept whole on purpose** — see below. |
| [`proposals.py`](proposals.py) | 3 | The desk's short list (Vexa-ai/vexa#1614): read the open rows, propose one, resolve one. |
| [`connections.py`](connections.py) | 11 | Connections for the agent: request and list a person's connected accounts, read Gmail and Calendar (one typed route per read: `gmail/search`, `gmail/inbox`, `gmail/read`, `gmail/thread`, `calendar/events`), create a Gmail draft, call a saved custom service, and `/api/onboarding/research`. Each is the route behind one tool in `core/agent/mcp.tools.v1.json`, served by the gateway's assembled MCP. Every route signs for the credential broker as the `agent` role through `control_plane/broker_client.py`; none takes or returns a credential, and every one but the status read refuses a worker dispatched without a person (declared `person` in `core/agent/routes.v1.json`, refused by `route_policy.PERSON_GATE`). |
| [`clock.py`](clock.py) | 2 | The person's clock: `GET /api/time` (`current_time`) and `PUT /api/time/zone` (`timezone_set`). The timezone is identity's fact, read and written at admin-api's `/internal/users/{id}/settings` over the internal tier. |
| [`workspaces.py`](workspaces.py) | 50 | Everything a workspace is: files, git state and its GitHub home, identity, the mount set, attach, swap and import, and the credentials that make a remote reachable. |
| [`sharing.py`](sharing.py) | 15 | Sharing a workspace: share-enable and unshare, create a group, invites (mint, preview, accept, list, revoke), the roster and roles, the two address-based verbs (`workspace_invite`, `workspace_membership`), leave, and the shared-with-me listing. |

**Where a named workspace is checked.** A worker dispatched without a person carries its dispatch's
workspace ceiling on the signed identity. The resolvers `create_app` hands every router —
`_read_target`, `_manage_dir`, `_ws_here` — and `ceiling.write_slug` refuse a workspace outside it
before resolving anything; a route that names a workspace without a resolver calls
`ceiling.require_in_ceiling` first. `tests/test_delegation_ceiling_verbs.py` generates its deny cases
from the built app's routes, so a route added with a workspace-naming parameter is checked the day it
lands.

## What PRD 40.7 does to this list

Decision 40.7 makes **agents optional**: *"meetings, agents and flows work independently and
together in any configuration"*, with identity the only shared dependency. Two of these routers are
therefore on notice, and the split is drawn so that moving one is a **file move, not a grep**:

- **`friction.py`** — the founder's open question right now. `whats_waiting` is moving to flows
  (decision 42.2), and the friction ledger is the other half of the same argument: it is filed by
  people and by agents, and a `no-agents` deployment still has people. Self-contained: one route,
  and the store already lives in flows.
- **`scaffolds.py`** — a scaffold composes an agent's first turn, so it reads as agent-domain; but
  it is minted by **flows** and read by the **terminal**, and the `no-agents` product still mails
  links. Not a decision this refactor makes.

`workspaces.py`, `sharing.py`, `chats.py`, `routines.py`, `ingress.py` and `admin.py` are agent-domain
by construction. `health.py` and
`meetings.py` stay wherever the service does.

## Order is not load-bearing here, and that is checked

FastAPI resolves **first-match-wins**, so regrouping routes into routers would be a behaviour
change if any two of them could match the same concrete URL under the same method. None can —
`tests/test_route_table.py::test_no_two_routes_can_match_the_same_url` asserts it on every run, so
it stays true as routes are added rather than being a property this refactor happened to have.
