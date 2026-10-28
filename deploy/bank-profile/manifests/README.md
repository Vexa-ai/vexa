# deploy/bank-profile/manifests — what the bank's platform team sets up

Applied by `../run.sh up` as the cluster admin, before anyone installs Vexa; never part of the chart.

- `platform.yaml` — the namespaces (the release namespace under Pod Security `restricted`), the
  restricted-v2 SCC emulation (a mutating webhook), the namespace's egress default-deny, and the
  namespace-scoped deployer with the Role the install guide's *Required permissions* names.
- `bank-ext.yaml` — the bank's own services: the OpenAI-compatible model, STT, the ADFS-like IdP,
  Postgres 16, and the workstation Pod the journey's person signs in from.

`__X__` placeholders are filled by `run.sh`.
