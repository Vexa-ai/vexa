- **The dispatch signing key has no published default (#1784).** agent-api signs each dispatch's
  identity token with `VEXA_DISPATCH_SIGNING_KEY`, which defaulted to `dev-dispatch-signing-key` on
  every deploy surface. agent-api now refuses to boot on an unset key, on that value, or on an
  internal-secret placeholder. `make all` mints one into `.env` and replaces the old value there; the
  Helm chart generates one and keeps it across upgrades when `secrets.dispatchSigningKey` is empty
  (with `secrets.existingSecretName`, carry a real `VEXA_DISPATCH_SIGNING_KEY`); Lite mints one on
  first boot and keeps it in the `vexa-lite-state` volume. Nothing verifies these tokens yet, so a new
  key changes nothing a running stack depends on. See [Configuration](/configuration#secrets--identity).
