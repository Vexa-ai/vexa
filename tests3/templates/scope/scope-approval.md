# Scope Approval — <release_id>

Status: draft
Stage: `scope-sign`

This is the human signing surface. A machine may later convert this document
into `scope-approval.yaml`, but the human should not have to edit YAML.

## What I Reviewed

- [Scope design](releases/<id>/scope-design.md) — release intent.
- [Scope document](releases/<id>/scope.md) — readable release contract.
- [Scope YAML](releases/<id>/scope.yaml) — machine release contract.
- [Scope verify](releases/<id>/scope-verify.md) — audit/pushback.

## My Summary

HUMAN WRITES:

> What am I approving, why now, and what trade-offs am I accepting?

## Attestations

The human checks these by editing `[ ]` to `[x]`.

- [ ] I read the scope more than once.
- [ ] I understand what this release does and why.
- [ ] I accept the trade-offs and believe this scope is deliverable.
- [ ] I authored or edited the final human-facing prose I am signing.

## Scope Items

The human checks each approved item by editing `[ ]` to `[x]`. Leave unchecked
items with a short note.

- [ ] `<scope-item-id>` — `<human-readable title>`
  - Note:

## Explicit Deferrals

- [ ] [`<deferred-item>`](<url-or-local-path>) — `<why deferred; target cycle>`
  - Note:

## Open Questions

Any open question blocks promotion to `develop-design`.

- `<question or "None">`

## Promotion Decision

- [ ] Proceed to `develop-design`.

Signer:

- Name:
- Role:
- Signed at:
- Git SHA:

