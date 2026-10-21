# helm · tests

Smoke tests for the `vexa` Helm chart: `test_helm_lint.sh` (chart lint) and `test_template.sh` (render/template validation, including the `storage.s3` wiring and the renders it must refuse). Run as part of the helm deploy checks.

Install-leg helpers (the `validate-helm-k3s` release leg uses both):

- `s3-fixture.yaml` — a TEST-ONLY S3 (versitygw, POSIX backend, pinned by digest) plus the Job that creates its `vexa-recordings` bucket. The chart runs no object store, so an install from `values-test.yaml` needs this running first; its header has the commands, including the random-credential Secret it reads.
- `recording_roundtrip.py` — run inside the meeting-api pod: uploads a recording the way a bot does, finalizes and plays it back through the gateway, deletes it, and lists the bucket prefix at each step.
