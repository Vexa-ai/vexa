# helm · tests

Static tests for the `vexa` Helm chart — no cluster required. Run by `make -C deploy/helm test` and by the Helm static-gates step in `release-images.yml`.

- **`test_helm_lint.sh`** — chart lint (default values + `values-test.yaml`).
- **`test_template.sh`** — the `gate:helm` render assertions: the carved control plane is present and correctly wired.
- **`test_mirror_only.sh`** — the mirror-only invariant ([#1006](https://github.com/Vexa-ai/vexa/issues/1006)). Renders `values-mirror-only.yaml`, which points every image the chart can cause a cluster to pull at one unresolvable internal registry, and fails if any container image, helm-hook image or **runtime-spawn reference** (`BROWSER_IMAGE` / `AGENT_IMAGE` / `AGENT_WORKER_IMAGE` — Pods the runtime creates later, which appear in no rendered manifest) falls outside it. It also proves the legacy `repository:tag` path is unchanged and that ambiguous or malformed image identities fail the render closed. Every assertion carries its own negative control.
