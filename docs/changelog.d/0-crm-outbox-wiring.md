- **The CRM's outbox runs on Compose and Helm.** Record changes now reach flows. Compose's `crm`
  profile adds a `crm-outbox` service. With `crm.enabled` and `flows.enabled`, the chart adds a
  `crm-outbox` Deployment. It signs card facts with the CRM's own Ed25519 key, which the chart
  generates (see the fact seal keypair entry). crm-api checks its configuration at boot and names
  every missing key. See [CRM](/minutes/crm).
