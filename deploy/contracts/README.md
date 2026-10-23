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
- **`meeting-bundle.v1`** — one meeting as a portable file any separate Vexa deployment can import: a zip
  with a manifest (a SHA-256 per file), the meeting, its transcript and annotations, and optional media and
  workspace parts, carrying nothing deployment-bound. meeting-api is its one writer (export) and its importer;
  the goldens include malicious bundles the Python importer and `validate.mjs` must refuse with the same
  code. It is a deploy contract because what it serves is the move between deployments.

Enforced by `gate:schema` + `gate:contract-version` (like every `*.vN`), plus `gate:execution-env`
(execution-targets.v1), `gate:config-contract` (config.v1) and `gate:fact-parity` (outbound-url.v1).
