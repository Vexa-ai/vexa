# deploy/contracts

Contracts owned by the **deploy** concern (P4 — a contract nests with its owner). Currently:

- **`execution-targets.v1`** — the host/user-specific execution-target & resource registry: where each plan
  stage may run and what external resources it needs (ADR-0020). Secrets are referenced, never inline (P14).
- **`config.v1`** — the per-service deployment-config contract (ADR-0026): each adopted service declares
  every env key it reads by class (`required-explicit` / `defaulted` / `capability`), vendors `preflight.py`
  verbatim as `config_preflight.py`, and refuses to boot on a missing required key. `gate:config-contract`
  ties declaration ≡ deploy surfaces (compose · helm · lite) ≡ code reads.
- **`outbound-url.v1`** — may a server fetch a URL somebody else supplied: the canonical `ssrf.py`, vendored
  verbatim into every Python image that fetches one, and the case table (golden) both the Python and the
  bot's TypeScript guard are held to. `gate:fact-parity` holds the copies (ADR-0044).

Enforced by `gate:schema` + `gate:contract-version` (like every `*.vN`), plus `gate:execution-env`
(execution-targets.v1), `gate:config-contract` (config.v1) and `gate:fact-parity` (outbound-url.v1).
