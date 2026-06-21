# identity — access · accounts · tokens · audit — authN/authZ, schema-agnostic

The identity lane owns authN/authZ for the platform.

## The authN/authZ model

Two questions, two mechanisms:

- **authN — "who are you?"** An opaque API token `vxa_<scope>_<random>` resolves to a `User`. The
  prefix is only a hint; the DB `api_tokens.scopes` column is authoritative (un-prefixed tokens are
  legacy = full access). The platform authenticates through ONE oracle — admin-api's
  **`/internal/validate`**: the gateway holds no identity itself, it POSTs each request's token there
  and gets back `{user_id, scopes, max_concurrent, email, webhook}`, then injects `X-User-ID` so
  downstream hops (meeting-api, collector) trust the resolved id and never see the raw token.
  **Fail-closed**: no `INTERNAL_API_SECRET` → 503; bad secret → 403; missing/invalid/expired token →
  401. There is no allow-on-error path.

- **authZ — "what may you do?"** TWO gates, both must pass:
  1. **capability (scope)** — does the token carry the scope the path needs? Scopes are the closed set
     `{bot, tx, browser}` (`VALID_SCOPES`); a mismatch is `missing-scope` (parent 403).
  2. **ownership (`canAccess`)** — `OwnerOnlyPolicy` is **default-deny** (P20): a subject may read only
     resources it OWNS (keyed on `user_id`); unknown owner / empty subject → denied. Guards the three
     read paths `meeting_transcript | recording | ws_subscribe`. Verdicts carry a stable reason code
     (`owner | not-owner | default-deny | missing-scope | token-expired`) sealed in `identity.v1`.

Three trust tiers gate the surface, each by its own header (constant-time `hmac.compare_digest`):
**admin** (`X-Admin-API-Key` → user/token CRUD), **user** (`X-API-Key` → self-serve), **internal**
(`X-Internal-Secret` → `/internal/validate`).

These rules live TWICE on purpose: as the live DB-backed `services/admin-api`, and as the pure,
DB-free `src/identity_core` reference — a `ScopedToken` value object *is* the validated identity; a
`canAccess` port replaces ownership checks otherwise scattered through route bodies. `identity.v1` is
the frozen contract both honor.

## Layout

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
