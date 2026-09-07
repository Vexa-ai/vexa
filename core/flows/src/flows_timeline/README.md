# `flows_timeline` — one person's day, in order

PRD decision 31. Founder, 2026-09-02: *"does the agent have temporal awareness of the last events
and future events? scheduled meetings, the things that actually get logged in the flows data"*.

The engine already writes every one of those moments down — a **reaction** is a fact that arrived,
an **effect receipt** is what we did about it — and nothing ever read them back along the one axis a
person thinks in. This package is that read, and it is only a read: it admits nothing, claims
nothing, runs nothing, and writes nothing.

| module | what it is |
|---|---|
| `model.py` | pure. Rows in, `Event`s out: the three mappers, the scoping rule, the merge. Everything that can be wrong in a way a test can catch. |
| `service.py` | the I/O: which rows to scan, who the subject is, and the meetings half over HTTP. |
| `render.py` | pure. The timeline as text, in the person's own zone — for the control-MCP `timeline` tool and for the per-dispatch preamble, which must not disagree. |

Served by `flows_integrations.flows_api` as `GET /timeline?subject=<uid|email>&since=&until=&limit=`
(`format=text|preamble` adds a rendered `text`). Read-only, and open to the operator key or to the
narrower `VEXA_FLOWS_TIMELINE_KEY`.

## Three things worth knowing before you change it

1. **Scoping needs BOTH identifiers.** The invite lineage carries an organizer address and no uid;
   the completed lineage carries a uid. Scoping on one silently returns half a day.
2. **Machinery is not an event.** Receipt steps become events only through `STEP_KINDS` — a
   whitelist, because the failure mode of a blacklist is a timeline that fills with `ensure_user`
   and still looks like a timeline. A **failed** receipt is always an event, whatever its step.
3. **One renderer.** The zone lookup and the formatting happen here, once, server-side. Two
   spellings of "half past two, their time" is how a chat and a machinery note end up disagreeing
   about one meeting.

`scripts/proof_timeline.py` runs the whole thing against a real lane database, read-only, with no
service up.
