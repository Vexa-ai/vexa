- **Breaking: boot refuses the published placeholder secrets (#1456).** A service whose
  `INTERNAL_API_SECRET` is one of the literals printed in this repo (`vexa-internal-secret`,
  `lite-internal-secret`, `changeme`, …) now stops at startup naming the variable, instead of
  running on a secret every reader of the source already has. Generate one per deployment
  (`openssl rand -hex 32`) and set the same value on every service before upgrading. Vexa Lite mints
  a random one on each boot and needs nothing set. `VEXA_FLOWS_API_KEY` is refused the same way and
  has no default at all. See [Configuration](/configuration).
