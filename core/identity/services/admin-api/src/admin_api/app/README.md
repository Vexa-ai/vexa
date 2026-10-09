# app — the admin-api FastAPI surface

`main.py` exposes `create_app()` with 3 auth tiers (admin `X-Admin-API-Key`, user `X-API-Key`,
internal `X-Internal-Secret`) and assembles the routers below.

- `main.py` — the admin tier (users, tokens), the user tier (`/user/*` self-serve), the instance and
  admin-claim doors, the person and membership doors, bot-context and model-config.
- `internal_tier.py` — the two `X-Internal-Secret` checks every `/internal/*` door uses (one with the
  dev-mode escape, one without).
- `validate.py` — the gateway's fail-closed authz oracle `POST /internal/validate`: an API key or a
  worker's delegation token in, one declared `ValidatedIdentity` out.
- `platform_settings.py` — `GET/PUT /internal/settings/{key}`: the per-key settings table, the field
  rulebook both tiers share, and the declared `PlatformSettingResponse`.
- `db.py` — an INJECTABLE async engine so the same app runs against testcontainers-PG or prod.
- `signin_allow.py` — the pure rule for who may sign in to the terminal (admin, claimed or named by
  `VEXA_ADMIN_EMAILS` · existing user · sign-in allow-list · the claim-code holder on an unclaimed
  instance) and who may claim the admin role, plus the lists' grammar. `main.py` serves it as
  `POST /internal/signin-admission` and `POST /internal/bootstrap-admin`; the admin-edited half of the
  allow-list is the `signin` platform setting.
- `signin_wire.py` — GENERATED from `core/identity/contracts/signin.v1` (`gen.mjs`): the sign-in
  wire's reason vocabularies and bounds, shared with the terminal's `signinWire.ts`.
- `claim_code.py` — the one-time admin claim code: issued at boot (`__main__.py`) and on
  `release-admin`, logged, stored as a digest in the `admin_claim` row, spent by the claim that uses it.
- `calendars.py` — the calendar connection value object stored in the user document; feed URLs are
  credentials and leave masked except on the internal hop.
- `person_settings.py` — person facts identity owns (timezone, mail preferences): read, validated
  write, and the one-shot import off `.settings.json`.
- `events.py` — the best-effort publish of `onboarding.completed` into flows.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
