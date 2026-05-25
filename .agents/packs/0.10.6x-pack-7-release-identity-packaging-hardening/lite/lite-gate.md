# Lite Gate

Status: source-level machine pass; human eyeball pending

The Lite Dockerfile embeds the same release identity path as the dashboard image:

- copies `VERSION` to `/repo/VERSION`;
- copies Helm `Chart.yaml` to `/repo/deploy/helm/charts/vexa/Chart.yaml`;
- sets `VEXA_REPO_ROOT=/repo`;
- runs `npm run assert-release-version` after `npm run build`.

Human Lite blast-radius eyeball validation is required before PR-ready status. See `lite/human-eyeball.md`.

Evidence:

- `lite/lite-identity-proof.json`
- `ops/lite-identity-proof/stdout.log`
- `ops/lite-identity-proof/stderr.log`
