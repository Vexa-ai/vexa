# context-stack — the four layers, their membership, and policy-routed writes

The context stack is the primitive a product-mode agent turn runs on: **global → group(s) →
personal → user-system**. This module owns its schema, its composition resolver, the router that
decides where a context delta lands, the owner's triage queue, and a write-only surface for
workspace secrets.

| Layer | Access | Content |
|---|---|---|
| **global** | read-only, hidden | product-level knowledge/behaviour, ours |
| **group** | read/write **via triage** | shared; writes land as **proposals the owner triages** — PR-style, never direct |
| **personal** | read/write | always exists for every user — *the user is not a group* |
| **user-system** | read, hidden, never sharable | sessions, chat history; holds no external credentials ever |

Start at [`src/context_stack/layers.py`](src/context_stack/layers.py): the table above is code,
once, and every rule in the module reads it rather than restating it.

## What each file is

| File | What it owns |
|---|---|
| `layers.py` | the four layers and their fixed access rules. One table, no second copy. |
| `models.py` | the six tables. Context is append-only; stack slots carry no foreign key; a decided proposal is required by a CHECK constraint to name a human. Each argued in place. |
| `store.py` | the repository. Persistence only — it decides nothing and it never commits. |
| `access.py` | who may do what, default-deny, pure. Every refusal carries a stable code. |
| `workspaces.py` | provisioning and membership. Group setup is a set of member emails. |
| `resolver.py` | composition. Pinned (the product) and free (the terminal) differ in one place. |
| `router.py` | the **machine path**: a delta lands where its workspace's policy says. |
| `triage.py` | the **human path**: an owner accepts or rejects. |
| `secrets.py` | set · rotate · delete · read metadata. Never a value. |
| `material.py` | the one function that reads secret material, alone in its own module. |
| `api.py` | an `APIRouter` a service mounts. Not an app; wires no database. |

## Three decisions worth knowing before reading the code

**Policy is the layer.** A workspace carries one `policy` field, and it answers both *where the
workspace sits in the stack* and *how a write to it lands*. Two fields could disagree, and the
only disagreement that matters — a group workspace accepting direct writes — is precisely the
bypass of owner triage the product forbids. One field cannot express it.

**Context is append-only.** Triage needs a before and an after; a rejected proposal must leave no
trace in context while leaving a full trace in the record; compaction can only ever be a readable
artifact over a history that was kept. It also matches what the knowledge store already is — these
workspaces are git repos, where a write has always been an append. Deletion is a tombstone.

**No machine ever writes acknowledgement.** Four guards, at four levels: `triage.py` depends on
`router.py`'s types, so the machine path importing the human path is a cycle Python refuses to
load; `actor` is required with no default and no service principal; the actor must hold the owner
role; and `ck_proposal_decision_is_attributed` refuses any decided row that does not name who
decided and when. There is no auto-accept, and adding one is not a small edit.

## The secret surface is write-only

Set, rotate, delete, and read metadata. Every management function returns `SecretMetadata`, which
has no field material could be put in — the surface is write-only because of the type, not because
of the callers. The one reader lives in `material.py`, is imported by neither `secrets.py` nor
`api.py`, and returns a handle whose `repr`, `str` and `format` are all redacted.

In the pilot exactly one secret is user-supplied: the LLM key or endpoint the workspace owner
brings. Self-hosted it is a k8s Secret and never reaches this table.

**Plaintext at rest**, deliberately — the level the saved GitHub token already sits at
(access-controlled, not encrypted). Envelope encryption is not built here: it earns its keep when
group-scoped integration tokens arrive with grant rows and expiries. Until then, a full server
compromise reads this column: use a revocable, minimally-scoped key.

## Running it

```bash
cd core/agent/modules/context-stack && uv run pytest -q     # 73 tests, no docker, no network
```

The tests build the real DDL on in-memory SQLite, so CHECK and UNIQUE constraints are exercised
rather than described. `pnpm gate:python` picks this package up mechanically (a `pyproject.toml`
plus a non-empty `tests/`).

## What is not here

No admin UI, no address allocation or mailbox provisioning, no LLM chat, no migration of existing
users, and no deployment wiring — the module owns no connection, and no service mounts it yet.
Because nothing creates these tables in a deployment, they are **not in `schema.seal.json`**;
sealing now would freeze a claim nothing is making. When a service adopts the module, add
`models.py` to `MODEL_FILES` in `scripts/schema_digest.py` and re-seal on a `lane:schema` review.

Contract: [`core/agent/contracts/context-stack.v1/`](../../contracts/context-stack.v1/README.md).
The existing terminal-side workspace model it will have to meet is documented at
[`docs/docs/core/workspaces.mdx`](../../../../docs/docs/core/workspaces.mdx); the product-side
model is [`docs/docs/core/context-stack.mdx`](../../../../docs/docs/core/context-stack.mdx).
