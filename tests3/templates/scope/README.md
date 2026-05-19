# Scope Artifact Templates

Canonical artifact set for the scope level:

```text
scope-design.md      # human intent
scope.md             # readable release contract
scope.yaml           # machine release contract
scope-verify.md      # audit/pushback
scope-approval.md    # human signed commitment
scope-approval.yaml  # optional generated machine projection
```

Use these templates in order:

1. `scope-design.md` during `scope-design`.
2. `scope.md` and `scope.yaml` during `scope-deliver`.
3. `scope-verify.md` during `scope-verify`.
4. `scope-approval.md` during `scope-sign`.
5. Generate `scope-approval.yaml` from `scope-approval.md` only if tooling
   needs a machine-readable projection.

The older names are compatibility aliases only:

- `groom.md` maps to `scope-design.md`.
- `plan-audit-findings.md` maps to `scope-verify.md`.
- `plan-approval.yaml` maps to generated `scope-approval.yaml`, not the human
  signing surface.

The human should never have to chase bare issue IDs. Every referenced issue,
PR, customer signal, log, or document must include a link or local path plus a
one-line explanation of why it is relevant.

The human should sign Markdown. YAML belongs to machines.

