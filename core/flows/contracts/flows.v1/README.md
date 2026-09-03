# `flows.v1` — the carrier census

A **carrier** is an event type with exactly one producing domain. The domain that knows the fact
publishes it, fire-and-forget, and a flow *definition* decides what happens next.

This is the only way two domains couple, and it works because **a publish edge is not a
dependency**: a dependency is a call whose answer the caller needs, a publish is a fact handed over.
A domain that publishes into flows therefore does not depend on flows, and runs with no flows
deployed at all — the facts are simply dropped.

`carriers.json` is the census. Registering a carrier promises three things at once — the owner, the
refs a consumer may rely on, and the cardinality. The third matters most:
`exactly_once_per_subject` means the producer holds a **durable stamp** and will not re-emit, and it
is required wherever a consumer takes an irreversible action. `onboarding.completed` triggers
billing on the paid product, so its stamp is written in the same transaction as the account it
describes — the guarantee is the stamp, not the code path, which is why it holds against a replay, a
restore, and a second producer somebody adds later.

| carrier | owner | cardinality |
|---|---|---|
| `onboarding.completed` | identity | exactly once per subject |
| `meeting.completed` | flows | once per occurrence |
| `invite.received` | flows | once per occurrence |
| `desk.unscaffolded` | agent | exactly once per subject |
| `claim.proposed` | agent | once per occurrence |

`meeting.completed` and `invite.received` are recorded from `core/flows/mcp.tools.v1.json`'s own
`publishes_events`; the two desk carriers from agent-api's `config.v1.json` publish-edge keys. None
of them is asserted afresh: this file is a reading of what the repository already declares, which is
what makes it a census rather than a wish.

`desk.unscaffolded` claims exactly-once and its stamp is **the desk itself** rather than a column.
That is a weaker guarantee than `onboarding.completed`'s and it is the right one here: the fact is
re-derivable from the workspace at any time (a directory with no `.scaffolded` in it), the consumer
takes no irreversible action — it puts a row on a queue — and a publish that never lands therefore
loses a card and never loses the fact.

## What reads it

- **`gate:config-contract`** — every `publish-edge` key in a service's `config.v1.json` names its
  carriers, and each must be registered here **owned by that service's domain**. Without the
  cross-check `publishes_events` would be a comment.
- **`gate:schema`** — `validate.mjs` checks the census against `flows.schema.json` and enforces one
  producing domain per carrier (JSON Schema cannot: `uniqueItems` compares whole objects, so two
  entries for one event with different owners both pass).
- **the flows suite** — `test_carrier_census.py` holds the census and the domain manifests in
  agreement in both directions.

## Registered, not yet sealed

It is registered in `architecture.calm.json` (node `flows.v1`) — `gate:dataflow`'s completeness
check requires every contract dir on disk to appear in the chart, so registration is not optional
and not deferrable.

It carries no entry in `contracts.seal.json`, so `gate:contract-version` reports it as *in
development* — and that part is deliberate. Sealing publishes a frozen `.vN`; freezing a shape on
the branch that introduces it would pin it before anybody has built a consumer against it. Re-seal
with `pnpm seal:contracts` in a `lane:contract` review once it has one.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
