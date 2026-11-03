- **Each fact producer signs with its own key.** The crm outbox now signs card facts with the CRM's
  own Ed25519 private key, and flows verifies under the public half. flows holds no key that can
  sign, and a leaked producer key cannot forge another producer's facts. `VEXA_FACT_SEAL_SECRET` and
  `secrets.factSealSecret` are gone, and your Secret no longer needs to carry
  `VEXA_FACT_SEAL_SECRET`. Compose generates the keypair on first start in the `fact-seal-keys`
  one-shot (`crm` profile). The chart generates it into the `fact-seal-crm-key` Secret and the
  `fact-verify-keys` ConfigMap. With `helm template` (GitOps), set `factSeal.crm.signingKey`, or set
  `factSeal.crm.existingSecret` together with `factSeal.crm.publicKey`. While you upgrade, a card
  fact sealed under the old secret is refused once and stays pending. The outbox signs it again with
  the new key on its next pass. See [CRM](/minutes/crm).
