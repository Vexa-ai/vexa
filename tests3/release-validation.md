# Release Validation

A scope-driven, 7-stage process. Each stage is **one command**. Drift is the enemy —
if you find yourself running tests ad-hoc, open an issue against this document.

## Branch model

- **`main`**: stable, `IMAGE_TAG=latest`. Always matches `:latest` on DockerHub.
- **`dev`**: active development, `IMAGE_TAG=dev`. Builds publish to `:dev`.

Merging `dev → main` overwrites `IMAGE_TAG=dev` into env-example; the
`ENV_EXAMPLE_LATEST_ON_MAIN` static lock catches this — fix it immediately
after merge (handled automatically by `make release-ship`).

## The scope file

Every release starts with a **scope file** at `tests3/releases/<id>/scope.yaml`.
It is the contract between the dev work and the validation:

```yaml
release_id: 260417-webhooks-dbpool
branch: dev
summary: ...

deployments:
  modes: [lite, compose, helm]         # which deployments must prove out the release

issues:
  - id: webhook-status-fast-path
    problem: "Status-change webhooks never fire on short meetings."
    hypothesis: "schedule_status_webhook_task isn't wired into the fast-path transitions."
    fix_commits: [0ac59fe]
    proves:
      - {test: webhooks, step: e2e_status, modes: [compose]}
    required_modes: [compose]
  # ... more issues

strict_features: [webhooks, infrastructure]
```

The scope drives every stage: which deployments to provision, which tests to
run in the iterate loop, which features must go strict in the gate.

See `tests3/releases/_template/scope.yaml` for the full schema.

## The 7 stages — one command each

| # | Stage | Command | What it does |
|---|-------|---------|--------------|
| 1 | Plan | `make release-plan ID=<slug>` | Scaffold `tests3/releases/<id>/scope.yaml` from the template. Fill in issues + fix_commits. |
| 2 | Provision | `make release-provision SCOPE=tests3/releases/<id>/scope.yaml` | Parallel: provision every deployment listed in `scope.deployments.modes` (Linode VMs + LKE cluster). Takes ~10 min. **Run in parallel with stage 3.** |
| 3 | Develop | *(local iteration)* | Edit code + tests + features/README frontmatter. Commit to dev. The `proves:` bindings in the scope tell you which test steps / checks must go green. |
| 4 | Deploy | `make release-deploy SCOPE=...` | Build & publish `:dev`, pull on each provisioned deployment, restart stack (keeps volumes). |
| 5 | Iterate | `make release-iterate SCOPE=...` | Run **only** the scope-filtered tests (per `issues[].proves[]`) on each mode. Writes reports to `tests3/reports/release-<tag>.md`. Loop: edit code → `release-deploy` → `release-iterate` until all `required_modes` in the scope go green. Fast (~2-3 min). |
| 6 | Full | `make release-full SCOPE=...` | Fresh-reset every deployment (wipe stack + volumes, keep VMs/cluster) → redeploy latest `:dev` → run the **full cheap-tier matrix** on all modes → aggregate → gate-check. This is the authoritative pass. |
| 7 | Ship | `make release-ship` | Push `release/vm-validated` GitHub status, PR dev→main, merge, fix `env-example` on main, promote all images `:dev → :latest`. |
| 8 | Teardown | `make release-teardown SCOPE=...` | Destroy every provisioned deployment (VMs + LKE cluster). |

### Why stages 2 and 3 run in parallel

Provisioning takes ~10 minutes (Linode + LKE). Development takes much longer.
Starting them sequentially wastes 10 min per cycle. Starting the provision
command early and iterating on code meanwhile means the deployment is ready
the moment your code is.

### Why iterate + full are separate

- **iterate** (stage 5): scope-filtered, fast, dirty state. You're debugging —
  you want fast feedback on *the specific thing you're fixing*.
- **full** (stage 6): fresh-reset, authoritative, every cheap test on every
  mode. This is what gates the merge. Running it once is enough; its job is
  to say "the whole product works on a clean install".

Running the full matrix on dirty state (from prior iterations) would mask
regressions that only show on first-boot state.

### Why fresh-reset, not reprovision

Reprovisioning VMs from scratch costs ~10 minutes per mode. Reset wipes
state (containers + volumes + DB) in ~30 seconds per mode. Over a release
cycle that's 50-60 minutes saved without weakening the gate (same clean
start from Postgres/Redis/MinIO's perspective).

## Stage details

### 1. `make release-plan ID=<slug>`

Creates `tests3/releases/<ID>/scope.yaml` from the template. Edit it:

1. Write one `issues:` entry per discrete problem you're fixing.
2. For each issue: problem statement → root-cause hypothesis → `fix_commits`
   (grow as you commit) → `proves:` bindings.
3. Set `deployments.modes` — if unsure, keep all three.
4. Set `strict_features:` for any feature whose gate should require 100% for
   this release (overrides the `confidence_min` in its README frontmatter).

### 2. `make release-provision SCOPE=...`

Reads `scope.deployments.modes`; for each mode:

- `lite` → Linode VM + install Docker + clone vexa + run `make lite`
- `compose` → Linode VM + install Docker + clone vexa + run `make all`
- `helm` → LKE cluster + `lke-setup-helm.sh` (provision + kubectl + helm install)

All modes run in parallel. Writes state to `tests3/.state-<mode>/`.

### 3. Develop (out-of-band)

Edit code + tests + `features/*/README.md` frontmatter. Commit to `dev`.
Every new DoD goes in the feature's README frontmatter `tests3.dods:` block;
every new test step goes in `tests3/test-registry.yaml` and in the test
script itself (via `step_pass`/`step_fail` from `tests3/lib/common.sh`).
No stdout parsing — JSON reports are the only truth.

### 4. `make release-deploy SCOPE=...`

1. `make release-build` — publishes `:dev` with a fresh timestamp tag.
2. For each mode in the scope: pull latest `:dev` + restart stack (keeps volumes).

### 5. `make release-iterate SCOPE=...`

For each mode in the scope: runs only the tests referenced in
`issues[].proves[]` for that mode. Reports land in
`tests3/.state-<mode>/reports/<mode>/<test>.json`. `release-report`
aggregates into `tests3/reports/release-<tag>.md` with per-feature
confidence.

Output lives at `tests3/reports/release-<tag>.md`. Every feature's DoD
table in `features/*/README.md` is auto-rewritten with live evidence
(idempotent — re-runs produce no diff if evidence unchanged).

If any required issue doesn't go green, edit code, `git commit`, `make
release-deploy SCOPE=…`, then re-run `release-iterate`.

### 6. `make release-full SCOPE=...`

1. `release-reset` → wipe every mode's stack + volumes (keeps infrastructure).
2. Redeploy latest `:dev` on each mode.
3. Run full cheap-tier `validate-<mode>` on each mode.
4. Aggregate into `tests3/reports/release-<tag>.md` + update feature READMEs.
5. Gate-check: fails if any feature is below its `confidence_min`.

### 7. `make release-ship`

Only run after `release-full` exits 0.

1. Push `release/vm-validated` commit status on HEAD (required by branch protection on main).
2. PR dev → main, merge.
3. Fix `env-example` on main (IMAGE_TAG=latest).
4. Promote `:dev` → `:latest` for every image.

### 8. `make release-teardown SCOPE=...`

Destroys every VM + LKE cluster listed in the scope. **Irreversible** — only
run after `release-ship` is green.

## Static regression locks (24 checks)

Independent of the release cycle. Run `make -C tests3 locks` to verify.
These are purely source-file checks, run on every deployment in the
`smoke-static` tier. Key:

- `ENV_EXAMPLE_LATEST_ON_MAIN` — IMAGE_TAG=latest in env-example
- `BROWSER_IMAGE_IN_ENV` — BROWSER_IMAGE set explicitly
- `NO_IMAGETOOLS_CREATE` — docker tag+push, not imagetools create
- `NO_DEV_FALLBACK_COMPOSE` — no silent :-dev fallbacks
- `NO_NESTED_COMPOSE_VARS` — no nested `${...}` in docker-compose
- `NO_EXPORT_IMAGE_TAG` — no Make-level export IMAGE_TAG
- `LITE_RECORDING_ENABLED` — recording on in lite
- `LITE_PROCESS_BACKEND` — process backend in lite
- `GATEWAY_TIMEOUT_ADEQUATE` — gateway timeout >= 30s
- `VM_LITE_USES_MAKE` — VM setup uses `make lite`

## GitHub gate

Branch protection on `main` requires the `release/vm-validated` status.
That status is per-commit — new commits reset it. Only `make release-ship`
(after `release-full` passed) sets it.

`enforce_admins` is off — repo admins can bypass (direct push shows
"Bypassed rule violations" warning).

## Escape hatches

- **Compatibility**: `make release-test SCOPE=...` is an alias for
  `release-provision && release-deploy && release-full`. Useful for a
  cold-start "full pipeline from zero" run.
- **Inspection**: each deployment's VM IP / kubeconfig is in `tests3/.state-<mode>/`. SSH in for
  debugging; nothing writes to those state dirs during an iterate loop.
- **Partial teardown**: `make -C tests3 vm-destroy STATE=…` removes just
  one VM; `lke-destroy STATE=…` removes just the LKE cluster.
