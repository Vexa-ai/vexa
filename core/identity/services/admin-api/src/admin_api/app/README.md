# app — the admin-api FastAPI surface

`main.py` exposes `create_app()` with 3 auth tiers (admin `X-Admin-API-Key`, user `X-API-Key`,
internal `X-Internal-Secret`) and the gateway's fail-closed `/internal/validate` oracle. `db.py`
builds an INJECTABLE async engine so the same app runs against testcontainers-PG or prod.
`signin_allow.py` is the pure rule for who may sign in to the terminal (admin, claimed or named by
`VEXA_ADMIN_EMAILS` · existing user · sign-in allow-list · the claim-code holder on an unclaimed
instance) and who may claim the admin role, plus the lists' grammar; `main.py` serves it as
`POST /internal/signin-admission` and `POST /internal/bootstrap-admin` and stores the admin-edited half
of the allow-list under the `signin` platform setting. `claim_code.py` is the one-time admin claim
code: issued at boot (`__main__.py`) and on `release-admin`, logged, stored as a digest in the
`admin_claim` row, spent by the claim that uses it.

_Governed by `docs/docs/governance/architecture.mdx` (P1–P12). This folder owns one concern; its public surface is its `index`/contract; it may depend only on what the dependency-rules allow._
