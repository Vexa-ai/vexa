# tests3 — Engine Contract

The public verb interface of the release engine. The stateless **pipeline** skills
(`infra:*`) bind to *these signatures*, not to `lib/` internals. Invoke from anywhere via
`make -C <repo> <verb> ARG=…`.

**Invariant (the skill↔engine seam):** every verb is a **pure function of its arguments** —
deterministic, idempotent, no judgment, no stage/`.current-stage` state. The *caller* (skill)
decides the args by reading the substrate, and interprets the result. The verb just does the
mechanics and reports honestly (machine-readable output + exit code). See the
`skill-engine-seam` doctrine.

---

## Verbs

| verb | args | does | output / exit |
|---|---|---|---|
| `build` | `ARTIFACT` (tag, optional) | build all images + push `:dev` — the only place bytes are created | pushed tag · `0` ok / non-zero build fail |
| `deploy` | `MODE`∈{lite,compose,helm} · `ENV`∈{local,throwaway,staging,prod} · `ARTIFACT` | stand the stack up (dispatch on MODE×ENV) | healthy stack · `0` ok / non-zero unhealthy |
| `validate` | `MODE` · `ENV` · `SCOPE`∈{full,`<scope.yaml>`} | run registry checks against the live stack | per-check report JSON under `.state/reports/<mode>/` · `0` green / non-zero red |
| `provision` | `MODE` | stand up fresh ephemeral infra (throwaway VMs / LKE) | infra handle · `0` ok |
| `teardown` | — | destroy ephemeral infra (mandatory after a throwaway cycle) | `0` (best-effort, zero residue) |
| `promote` | `FROM` · `TO` (e.g. `:dev`→`:latest`) | re-tag the artifact forward — **never rebuilds** | re-tagged · `0` ok |
| `publish-packages` | — | build + publish `packages/*` to npm (idempotent) | `0` ok |
| `helm-upgrade-safe` | `RELEASE_NAME` `NAMESPACE` `CHART_PATH` `VALUES_FILES` | pre-flight image-exists check + atomic helm upgrade | `0` ok / non-zero (aborts before upgrade if any image missing) |

## The MODE × ENV matrix (what's wired)

```
                 local          throwaway           staging / prod
lite       deploy/lite       tests3 vm-*-lite          —
compose    deploy/compose    tests3 vm-*-compose       —
helm       (TODO: kind)      tests3 lke-*          vexa-platform (cross-repo, TODO)
```

- `local` → `deploy/{compose,lite}` directly.
- `throwaway` → `tests3` VM (`vm-provision-*`/`vm-redeploy-*`) and LKE (`lke-*`) mechanics.
- `staging`/`prod` **helm** → lives in **vexa-platform**, not this engine. `deploy MODE=helm ENV=staging`
  exits 2 with a pointer (see `helm-validation-env` doctrine: helm validates in vexa-platform
  staging by default).

## What's deliberately NOT here

No stage machine: no `.current-stage`, no `stage.py`, no `release-{groom,plan,develop,…,ship}`
targets. Orchestration — *which* verb to run *when* — is the **pipeline** skills' job; the engine
only exposes the mechanics. State lives in the substrate (pack tags, PRs, branches, image tags),
not a marker file.
