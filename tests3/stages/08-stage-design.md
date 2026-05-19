# Stage: stage-design

**Level:** stage · **Role:** design · **Inner-loop:** `design → deliver → verify → sign`

| field        | value |
|--------------|-------|
| Actor        | AI + human where infra/risk choices matter |
| Objective    | Decide the throwaway production-like environment shape before provisioning it. |
| Inputs       | develop-signed artifact, required modes, scope-bound proves, infra constraints. |
| Outputs      | stage plan: modes, image tags, env topology, validation matrix, handoff URLs expected. |

## Steps

1. `lib/stage.py assert-is stage-design`.
2. Resolve required modes (`lite`, `compose`, `helm`) from scope.
3. Decide fresh throwaway infra shape and env topology.
4. Confirm image tag rules: stage pulls release `:dev` artifacts, not local
   mutable builds.
5. Confirm what human-visible URLs/artifacts `stage-sign` will need.

## Exit

The canonical throwaway infra run is designed and can be provisioned by
`stage-deliver`.

## May NOT

- Deploy or validate yet.
- Replace helm/stage proof with local proof for helm-bound claims.
- Promote public release artifacts.

## Next

`stage-deliver`.

