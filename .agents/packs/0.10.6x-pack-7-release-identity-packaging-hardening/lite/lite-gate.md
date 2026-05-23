# Lite Gate

Status: source-level pass, runtime validation deferred

The Lite Dockerfile embeds the same release identity path as the dashboard image:

- copies `VERSION` to `/repo/VERSION`;
- copies Helm `Chart.yaml` to `/repo/deploy/helm/charts/vexa/Chart.yaml`;
- sets `VEXA_REPO_ROOT=/repo`;
- runs `npm run assert-release-version` after `npm run build`.

The pack epic says Lite image/version identity should be validated after product pack stitching. This pack records the source-level identity proof now and leaves runtime Lite image validation for stitched candidate evidence.

Evidence:

- `lite/lite-identity-proof.json`
- `ops/lite-identity-proof/stdout.log`
- `ops/lite-identity-proof/stderr.log`
