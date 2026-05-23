---
name: vexa-lite-deploy
description: Deploy and verify Vexa Lite using only the official Vexa upstream deploy-folder README files. Use when Codex is asked to set up, run, inspect, debug, or stop the single-container Vexa Lite deployment using the deploy/lite make workflow.
---

# Vexa Lite Deploy

## Start Here

Read `references/official-docs.md` first. Then read the upstream official sources it names before running or changing anything:

1. `deploy/README.md`
2. `deploy/lite/README.md`

## Rules

- Follow the deploy-folder `make lite` flow from the upstream repo root.
- Do not use docs-site `docker run` examples for this skill.
- Do not use Docker Compose deployment commands except where the Lite README compares modes or points to stopping/debugging Lite.
- Do not add helper scripts or alternative deployment recipes.
- Do not print transcription tokens, admin tokens, database credentials, or any secret values.
- If the official docs are missing, contradictory, or do not cover the requested environment, stop and report the exact documentation gap.

## Deploy Flow

1. Confirm the target checkout is the upstream `Vexa-ai/vexa` repo or clone it using the official deploy docs.
2. From the upstream repo root, run `make lite`.
3. Let the official flow provision PostgreSQL, pull/start the Vexa Lite image, configure transcription, and verify connectivity.
4. When prompted for a transcription token, use a user-provided token without echoing it.

## Completion Criteria

Treat the deploy as incomplete until the official post-start checks are satisfied:

- `make lite` completes successfully.
- API docs are reachable at the documented API docs URL.
- Dashboard is reachable at the documented dashboard URL.
- Lite logs show post-startup health when inspected.
- `docker exec vexa-lite supervisorctl status` shows expected services when deeper verification is needed.

Use `make lite-down` when the user asks to stop the Lite deployment. Report URLs, checks run, and final pass/fail state. Redact secrets.
