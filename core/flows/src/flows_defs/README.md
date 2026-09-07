# flows_defs — the flows themselves

Product behavior as DATA: each flow is a typed event trigger plus an ordered list of step
FUNCTIONS (a typo is a registration error, never a 2pm KeyError; strings exist only in the
database). One file per flow; reviewed like any product change; a new version is a new
registration — in-flight reactions keep the version stamped at admission, new events select the
newest (Registry.match).

## `production.py` and `production_agent.py`

The production definitions are in TWO modules, split by one property: whether the flow still does
anything when the **agent domain is not deployed** (PRD decisions 40.6/40.7).

| | Flows | With no agent domain |
|---|---|---|
| `production.py` | `invite_intake` · `post_meeting` · `live_meeting` | they still run — the invite is accepted, the bot joins, the meeting is recorded, and the agent-reaching steps answer `agent:not_present` |
| `production_agent.py` | `meeting_prep` · `email_chat` · `desk_setup` · `desk_claim` · `workspace_invite` | **not registered at all** — a conversation with an agent, two cards on a desk and a fact only agent-api publishes have nothing to degrade to |

The split is by **whether the flow still does anything**, and `workspace_invite`
(Vexa-ai/vexa#1632) is the one that shows the property is not "does the step touch agent-api".
Its step reaches no domain at all — it renders a template and sends one notification — so it
declares no `needs=` and would run perfectly well in a deployment with no agents. It belongs on
the right-hand row anyway, on `desk_setup`'s ground: the PRODUCING domain is the agent domain, so
where there is no agent-api the fact is never published and the flow would exist to do nothing.

`production.build()` calls `production_agent.build()` last, and only when
`flows_steps.common.domain_present("agent")` — the same predicate the engine consults for every
`needs=("agent",)` step, reading the same key (`VEXA_FLOWS_AGENT_API_URL`) and never probing. A cut
that deletes `production_agent.py` outright is supported: the seam checks `find_spec` first.

Shared helpers stay in `production.py`, and `production_agent` reads every collaborator **through
the module object handed to its `build(reg, db, home=…)`** — never `from .production import …`. One
`monkeypatch.setattr(production, …)` has to reach both halves.
