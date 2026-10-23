- **Bank-ready now has a definition: the bank profile.** A release is called bank-ready only when
  the restricted reference environment in `deploy/bank-profile` is green on its exact commit: a
  non-superuser Postgres on both role paths, non-root Pods under `restricted-v2` rules, no egress but
  declared endpoints, a corporate CA, sign-in without `email_verified` and no Anthropic endpoint,
  installed by a namespace-scoped deployer and used from sign-in to a meeting bot. See
  [What we test against](/deployment-kubernetes#what-we-test-against).
- **A release named so its Services start with `vexa-` no longer crash-loops the model proxy.** With
  `fullnameOverride: vexa`, Kubernetes handed every Pod service-link variables such as
  `VEXA_AGENT_API_PORT=tcp://…`, which the services read as their own `VEXA_*` settings. No chart
  Pod receives service-link variables now.
- **The deployer's documented rights include reading ReplicaSets**, which `helm install --wait` and
  `helm upgrade --wait` need. See [Required permissions](/deployment-kubernetes#required-permissions).
