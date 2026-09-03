# `carriers.draft` — flows.v1, before it is a contract

**UNVALIDATED. `node validate.mjs --check` has never completed in this worktree** — treat the schema
and both goldens as a draft until it does.

A **carrier** is an event type with exactly one producing domain. The domain that knows the fact
publishes it, fire-and-forget, and a flow *definition* decides what happens next. This is the only
way domains couple: **a publish edge is not a dependency**, so a domain that publishes into flows
does not depend on flows and works with no flows deployed at all.

Registering a carrier is a promise about three things at once — the owner, the payload a consumer
may rely on, and the cardinality. The third is the one that matters most: `exactly_once_per_subject`
means the producer holds a durable stamp and will not re-emit, and it is required wherever a
consumer takes an irreversible action. `onboarding.completed` triggers billing on the paid product,
so its stamp is written in the same transaction as the account it describes.

| carrier | owner | cardinality |
|---|---|---|
| `onboarding.completed` | identity | exactly once per subject |
| `meeting.completed` | meetings | once per occurrence |

`meeting.completed` is here as the second entry so the shape is not a description of one event. It
has been published since long before this contract existed; writing it down is what makes this a
census rather than a wish.

---

**Why it is not in  yet.** A registered contract dir must appear in the sealed
, and sealing the architecture chart is a human review step, not something a
branch does on its way past. It moves to  in the same change that
registers it — after  has actually run.

---

**Why it is not in `contracts/` yet.** A registered contract dir must appear in the sealed
`architecture.calm.json`, and sealing the architecture chart is a human review step — not something
a branch does on its way past. It moves to `core/flows/contracts/flows.v1/` in the same change that
registers it there, after `validate.mjs` has actually run against these goldens.
