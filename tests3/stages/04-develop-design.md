# Stage: develop-design

**Level:** develop · **Role:** design · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | human + AI |
| Objective    | Reconcile approved scope with implementation reality before changing code. |
| Inputs       | signed scope, prior verify/sign bounce, local constraints, implementation discoveries. |
| Outputs      | amended scope decisions or a short develop-design note under `releases/<id>/`. |

## Steps

1. `lib/stage.py assert-is develop-design`.
2. Read the signed scope and any bounce reason.
3. Decide whether the implementation can proceed as scoped.
4. If the design changed, update the release doc/companion through the signed
   scope mechanism; otherwise record that no design amendment is needed.

## Exit

The implementation path is clear enough for `develop-deliver`.

## May NOT

- Hide scope changes inside code.
- Add fallback/workaround behavior without an explicit decision.
- Mark human approval fields true.

## Next

`develop-deliver`.

