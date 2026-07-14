# deploy/contracts

Contracts owned by the **deploy** concern (P4 — a contract nests with its owner). Currently:

- **`execution-targets.v1`** — the host/user-specific execution-target & resource registry: where each plan
  stage may run and what external resources it needs (ADR-0020). Secrets are referenced, never inline (P14).
- **`config.v1`** — the per-service deployment-config contract (ADR-0026): each adopted service declares
  every env key it reads by class (`required-explicit` / `defaulted` / `capability`), vendors `preflight.py`
  verbatim as `config_preflight.py`, and refuses to boot on a missing required key. `gate:config-contract`
  ties declaration ≡ deploy surfaces (compose · helm · lite) ≡ code reads.

Enforced by `gate:schema` + `gate:contract-version` (like every `*.vN`), plus `gate:execution-env`
(execution-targets.v1) and `gate:config-contract` (config.v1).
