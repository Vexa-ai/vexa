# execution-targets.v1

**Concern:** the machine-readable answer to *"where can work run, and what does it need?"* — the registry a
plan resolves its `Runs on:` / `Resources:` against, **in planning, before execution** (ADR-0020). Promotes
Learning #22 (*the amd64 bot's host is `bbb`; consult the registry before escalating a "block"*) to a contract.

**Surface:** `execution-targets.schema.json` (the shape) · `golden/` (the spec, P8) · `validate.mjs`
(gate:schema; also validates the deploy registry files via `--file`).

**Shape:** `targets[]` = {name · kind (ssh/local/ci/k8s) · arch · caps (docker·compose·amd64-bot·gpu·redis·…)
· provision?} · `resources[]` = {name · kind (service/credential-set/storage/meeting/human-gate) · endpoint? ·
`secret_ref`? · env?}. **`secret_ref` is a REFERENCE only** (`vexa-secrets:<path>` / `env:<NAME>`) — never a
secret value (P14), enforced by the schema pattern.

**Files (P14 / ADR-0002):**
- `deploy/execution-targets.example.json` — committed template (the bbb + ci seed).
- `deploy/execution-targets.json` — the user's real registry, **gitignored**; copy the template, fill in,
  reference secrets from `~/dev/vexa-secrets`.

**Depends on:** nothing (a leaf contract). Consumed by: `scripts/gates.mjs` (`gate:execution-env`) and the
planning preflight.
