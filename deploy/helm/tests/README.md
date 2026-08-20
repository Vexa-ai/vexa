# helm · tests

Smoke tests for the `vexa` Helm chart: `test_helm_lint.sh` (chart lint), `test_template.sh`
(render/template validation), and `test_image_digests.sh` (immutable image references and
fail-closed tag/digest ambiguity). Run as part of the Helm deploy checks.
