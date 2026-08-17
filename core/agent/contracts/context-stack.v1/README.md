# context-stack.v1 — the four layers a product-mode turn composes — UNSEALED

Every product-mode agent turn runs one composition, and this contract is its vocabulary.

| Layer | Access | Content |
|---|---|---|
| **global** | read-only, hidden | product-level knowledge/behaviour, ours |
| **group** | read/write **via triage** | shared; writes land as **proposals the owner triages** — PR-style, never direct |
| **personal** | read/write | always exists for every user — *the user is not a group* |
| **user-system** | read, hidden, never sharable | sessions, chat history; holds no external credentials ever |

Three things in here are decisions rather than description:

- **`policy` is one field, and it is also the layer.** A workspace's policy answers both *where it
  sits in the composition* and *how a write to it lands*. Two fields could disagree, and the only
  disagreement that matters — a group workspace taking direct writes — is exactly the bypass of
  owner triage the product forbids.
- **A `ResolvedStack` is pointers, not content.** Each slot names a workspace and its layer's
  access rules; reading a document is a separate, separately-authorised act. That is what lets a
  user be re-pointed at a different personal workspace, or compose several groups, without any
  shape here changing.
- **`SecretMetadata` is the whole read surface of a workspace secret.** No shape in this contract
  has a field secret material could ride in. Setting a secret is the only place a value appears,
  and it appears going in.

Shapes: `Policy` · `Write` · `Role` · `ProposalState` · `Workspace` · `Membership` ·
`ContextRevision` · `Proposal` · `StackSlot` · `Denial` · `ResolvedStack` · `ContextDelta` ·
`Routing` · `AccessDecision` · `SecretMetadata`.

Implemented by [`core/agent/modules/context-stack/`](../../modules/context-stack/README.md),
whose `to_contract()` methods emit these shapes — its `tests/test_contract.py` validates live
output against this schema, so the contract cannot drift from the code while the goldens still
pass. There is no entry in `contracts/loader.py`: nothing validates these at runtime yet, and a
validator no caller reaches is worse than none.

**Status: UNSEALED** (in development) — not yet pinned in `contracts.seal.json`; `gate:schema`
validates its goldens, `gate:contract-version` reports it unsealed. Sealed via
`pnpm seal:contracts` on a `lane:contract` review.
