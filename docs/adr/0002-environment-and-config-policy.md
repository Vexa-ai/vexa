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

## Recorded exceptions to the `VEXA_*` name

Each is declared in its service's `config.v1.json`, so `gate:config-contract` still holds it on every
surface; only the name departs from the rule.

- **`RUNTIME_API_TOKEN`** (2026-10-09, v0.13.2) — the runtime caller credential. One name across its
  three holders (the runtime, agent-api, meeting-api) and the compose/Helm secret key, so an operator
  sets one value under one name; agent-api reads it through an explicit alias (`shared/config.py`).
- **`DOCKER_WORKER_NETWORK`** (2026-10-09, v0.13.2) — the network the docker backend puts agent
  workers on, beside the runtime's existing `DOCKER_NETWORK` for bots. It joins that unprefixed
  substrate family rather than splitting it.
