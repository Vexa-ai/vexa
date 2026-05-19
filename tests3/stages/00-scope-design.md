# Stage: scope-design

**Level:** scope · **Role:** design · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | human + AI |
| Objective    | Choose and shape the release intent in human language: why, what, and how. |
| Inputs       | GitHub issues, production logs, emails, customer signals, Discord/support context, prior release aftermath, strategy. |
| Outputs      | `releases/<id>/scope-design.md` — concise release intent and selected issue packs, using `tests3/templates/scope/scope-design.md`. |

## Breathing Model

The inner loop starts concise and human-centric:

```text
why -> what -> how
```

Then later stages expand it into implementation scaffolding, push back with
machine/audit feedback, and return it to the human as a signable claim.

Human involvement happens here at design time and later at `*-sign`, where the
human judges whether the delivered artifact still matches the design with no
regressions or unowned gaps.

## Steps

1. `lib/stage.py assert-is scope-design`.
2. Gather planning-environment signal: issues, production evidence, customer
   signal, emails, support/Discord, prior release trail, and strategic urgency.
3. Cluster the signal into candidate packs.
4. Mark what is in scope, explicitly out of scope, and deferred.
5. Write `releases/<id>/scope-design.md` from
   `tests3/templates/scope/scope-design.md`: why this release exists, what it
   includes, what is excluded, and how the work should be constrained.

## Exit

`releases/<id>/scope-design.md` exists and a human has selected at least one
pack or release intent to expand.

The document must let a human answer:

- Why does this release exist?
- What is in and out?
- What design stance constrains implementation?
- What must be expanded by `scope-deliver`?

## May NOT

- Edit product code.
- Pretend implementation details are already proved.
- Auto-approve the release scope.

## Next

`scope-deliver`.
