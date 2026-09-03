# agent · control_plane · routers

The 78 HTTP routes agent-api serves, one module per **owner**. `api.py`'s `create_app` was 2,868
lines and held all of them, which is why every lane that touched agent-api touched one file — and
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
| [`health.py`](health.py) | 2 | `/health` and `/api/version` — the two answers that must work when nothing else does. |
| [`chats.py`](chats.py) | 10 | The conversation surface: `/invocations`, the `/api/chat` SSE turn, `/api/sessions*`, the `/events` artifact sink, and `/api/routines*` — the clock that wakes agents. |
| [`admin.py`](admin.py) | 7 | The operator's surface: `/api/admin/*` (the hidden panel), `/api/global/*` (the organisation tier), and the two credential self-tests. Internal-tier gated; not a user surface. |
| [`meetings.py`](meetings.py) | 2 | The meeting seam: relay health, and the live transcript stream a chat renders beside the conversation. |
| [`scaffolds.py`](scaffolds.py) | 7 | One record per arrival (PRD §5.5): mint, read, redeem the transcript share — plus the two reads a panel does around it, `/api/links/resolve` and `/api/desk/touch`. |
| [`friction.py`](friction.py) | 3 | The rough-edges ledger (PRD decision 33). **Kept whole on purpose** — see below. |
| [`workspaces.py`](workspaces.py) | 47 | Everything a workspace is: files, git state, identity, the mount set, attach and swap, sharing, membership, invites, and the credentials that make a remote reachable. |

## What PRD 40.7 does to this list

Decision 40.7 makes **agents optional**: *"meetings, agents and flows work independently and
together in any configuration"*, with identity the only shared dependency. Two of these routers are
therefore on notice, and the split is drawn so that moving one is a **file move, not a grep**:

- **`friction.py`** — the founder's open question right now. `whats_waiting` is moving to flows
  (decision 42.2), and the friction ledger is the other half of the same argument: it is filed by
  people and by agents, and a `no-agents` deployment still has people. Self-contained, three
  routes, one store.
- **`scaffolds.py`** — a scaffold composes an agent's first turn, so it reads as agent-domain; but
  it is minted by **flows** and read by the **terminal**, and the `no-agents` product still mails
  links. Not a decision this refactor makes.

`workspaces.py`, `chats.py` and `admin.py` are agent-domain by construction. `health.py` and
`meetings.py` stay wherever the service does.

## Order is not load-bearing here, and that is checked

FastAPI resolves **first-match-wins**, so regrouping routes into routers would be a behaviour
change if any two of them could match the same concrete URL under the same method. None can —
`tests/test_route_table.py::test_no_two_routes_can_match_the_same_url` asserts it on every run, so
it stays true as routes are added rather than being a property this refactor happened to have.
