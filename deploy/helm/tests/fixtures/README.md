# Helm test fixtures

Static values files used only by the chart render gates. `values-image-digests.yaml` uses synthetic
SHA-256 values to prove immutable-reference rendering and never names a runnable release artifact.
