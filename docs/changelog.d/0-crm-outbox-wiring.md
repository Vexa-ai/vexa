- **The CRM's outbox runs on Compose and Helm.** Record changes now reach flows. Compose's `crm`
  profile adds a `crm-outbox` service. With `crm.enabled` and `flows.enabled`, the chart adds a
  `crm-outbox` Deployment. It seals card facts with `VEXA_FACT_SEAL_SECRET`, which the chart now
  generates (`secrets.factSealSecret`) and gives to flows-api too. With `secrets.existingSecretName`,
  add `VEXA_FACT_SEAL_SECRET` to your Secret. crm-api checks its configuration at boot and names
  every missing key. See [CRM](/minutes/crm).
