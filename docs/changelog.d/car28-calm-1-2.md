- **The architecture model is CALM 1.2 and every control it names resolves.** `architecture.calm.json`
  validates on calm-cli 1.65.0 with no errors: each control names its requirement schema by repository
  path under `calm/controls/`, so `calm validate -a architecture.calm.json` from the repository root
  checks every control's configuration, and `gate:calm` fails when one does not resolve. Agent model
  calls and customer webhook deliveries are now modelled as egress edges with their controls. See
  [Architecture as Code](/architecture/architecture-as-code).
