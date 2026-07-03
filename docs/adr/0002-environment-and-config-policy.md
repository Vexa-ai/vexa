# ADR 0002 — Environment & config policy (P14)

**Status:** accepted · 2026-06-18

## Context
Workers are configured by env (P7), but we had no convention — `BOT_CONFIG`, ad-hoc vars, secrets in
env, no validation.

## Decision
- **Naming:** all app vars `VEXA_*`, SCREAMING_SNAKE_CASE.
- **Structured config = one JSON env var validated against a `*.v1` schema.** The bot's config is
  `invocation.v1` in `VEXA_BOT_CONFIG` (renamed from `BOT_CONFIG`). Only a few primitive bootstrap
  vars (redis URL, callback URL, identity token) are individual.
- **Secrets are a class** (`*_TOKEN`/`_SECRET`/`_KEY`/`_PASSWORD`): never logged, committed, or in
  goldens (placeholders only); injected by the orchestrator's secret store; regulated deployments
  carry a secret-store *reference*, resolved at boot.
- **Validate at boot, fail fast** (zod/envalid in TS, pydantic-settings in Python); no scattered
  `process.env.X ?? fallback`. A committed `.env.example` documents the contract.

## Consequences
- Each workload's env contract is part of its `invocation.v1`; the kernel's `env` stays opaque (P11).
- `BOT_CONFIG → VEXA_BOT_CONFIG` when `invocation.v1` is sealed.
