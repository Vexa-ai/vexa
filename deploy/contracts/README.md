# deploy/contracts

Contracts owned by the **deploy** concern (P4 — a contract nests with its owner). Currently:

- **`execution-targets.v1`** — the host/user-specific execution-target & resource registry: where each plan
  stage may run and what external resources it needs (ADR-0020). Secrets are referenced, never inline (P14).

Enforced by `gate:schema` + `gate:contract-version` (like every `*.vN`) and `gate:execution-env`.
