# queue — what a person is told is waiting

`whats_waiting` is the first call a person's own agent makes, and this directory is what it says.
The route (`GET /queue/waiting` on flows-api) returns **data**: which reaction, which flow produced
it, which step it is on, and a typed reason. Every sentence a human reads comes from here.

## The lookup

For each of the subject's pending reactions, in order:

1. `<flow>.<reason type>.md`
2. `_<reason type>.md`

resolved against `$VEXA_BEHAVIOR_DIR/queue/` first (the private tree — the product's real voice),
then this published showcase. First non-empty file wins.

The reason types are the four in `core/flows/src/flows_queue.py`:

| type | when |
|---|---|
| `human` | the reaction is `blocked` — somebody has to answer before anything moves |
| `failed` | it failed, with a reason |
| `not_present` | the engine ended it because a domain is not deployed here (PRD decision 40.7) |
| `pending` | in flight |

## Silence is the filter

**A reaction no file matches is counted, not spoken.** That is deliberate and it is the whole
design: a person's reactions include a lot of plumbing — an invite parked three hours before the
call it is waiting for is pending, and is nobody's business — and the alternative to this is a
keyword list inside a tool deciding what is interesting.

So: adding a file is how a situation becomes person-facing, and deleting one is how it stops. Both
are commits here, and neither is a deploy. The count of silent reactions comes back as `quiet`, so
an operator can always tell *nothing is happening* from *behavior is saying nothing about it*.

## Front-matter: `notice: true`

A file may open with a fenced block of `key: value` lines. There is one key:

```markdown
---
notice: true
---
Whatever a person's agent should keep in front of it.
```

**A notice is something that stays TRUE BETWEEN calls, not something that just happened.** An
ordinary item is read when an agent asks what is waiting. A notice is read alongside whatever the
agent was already doing: `GET /queue/notices` returns just these files' words, it is small enough
to ask on every call, and the MCP edge carries the answer out on the results of the meeting tools
(`core/meetings/services/mcp/src/vexa_mcp/notices.py`). So the same sentence reaches an agent that
never asked — which is the point, and also the reason to flag almost nothing.

Everything else stays as it is: a flagged file is still an ordinary item on `GET /queue/waiting`
(now carrying `notice: true`), the lookup is unchanged, and a file with no front-matter — which is
every file written before this existed — declares nothing and behaves exactly as it did.

Two behaviours worth knowing: **a value that is not `true`/`yes`/`on`/`1` means no** (a flag nobody
can read is off, never guessed on), and **an unparseable fence leaves the words intact and the flag
off** — the failure of a flag must never be the failure of a sentence a person was owed.

## What is deliberately not here

There is **no first-run friction ask** (ruling 8/9, 2026-09-03): *a flow that fires once per person
is a heavy way to say hello.* Rough edges are reported when they happen, not asked about on arrival.

## Writing one

Address the person's agent, not the person: these files tell it what is true and what to offer, and
it says it in its own voice to someone who has never heard of a reaction. Never name a tool the
person does not have, never explain our plumbing, and keep it short — this is read on every call.
