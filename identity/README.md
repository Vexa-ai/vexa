# identity — access · accounts · tokens · audit — authN/authZ, schema-agnostic

The identity lane owns authN/authZ for the platform. Layout:

- **`contracts/identity.v1/`** — the sealed wire shapes: `ScopedToken` (subject + scopes + expiry)
  and `AccessDecision` (the `canAccess` verdict). gate:schema + gate:contract-version.
- **`src/identity_core/`** — the CORE (this lane's `index`): scoped tokens (`tokens.py`), the
  `canAccess` authz port + default-deny owner-only adapter (`access.py`, P20), and the `SecretsPort`
  credential broker (`secrets.py`, P15). Pure, DB-free, dependency-light. Why here and not under
  `services/`: it is a reusable library of policy/broker primitives, not a long-running deployable —
  the runnable carve (`services/admin-api/`, users + tokens + `/internal/validate`) is owned by a
  separate stream and consumes these primitives.
- **`tests/`** — pure unit evals riding gate:python (incl. the `gate:access` deny-tests).
- **`services/`** — runnable identity services (admin-api carve, Group 1).

_Governed by `docs/ARCHITECTURE.md` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
