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

## What is deliberately not here

There is **no first-run friction ask** (ruling 8/9, 2026-09-03): *a flow that fires once per person
is a heavy way to say hello.* Rough edges are reported when they happen, not asked about on arrival.

## Writing one

Address the person's agent, not the person: these files tell it what is true and what to offer, and
it says it in its own voice to someone who has never heard of a reaction. Never name a tool the
person does not have, never explain our plumbing, and keep it short — this is read on every call.
