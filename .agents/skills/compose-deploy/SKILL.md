---
name: compose-deploy
description: Deploy and verify the Vexa Docker Compose stack using only the official Vexa upstream deploy-folder README files. Use when Codex is asked to set up, run, inspect, debug, test, stop, or clean up the full Vexa compose deployment from deploy/compose. When invoked without a narrower explicit action, proceed with the normal deploy-and-verify flow.
---

# Compose Deploy

## Start Here

Read `references/official-docs.md` first. Then read the upstream official sources it names before running or changing anything:

1. `deploy/README.md`
2. `deploy/compose/README.md`

## Default Behavior

If this skill is invoked without a specific action, do not ask the user what to do. Treat the request as: deploy the full Compose stack, verify it through the official Makefile workflow, and report the running URLs and verification status.

Only ask a clarifying question when the missing information cannot be discovered from the checkout or official docs and acting would risk changing the wrong system. Otherwise, choose the documented normal path and keep moving.

## Rules

- Follow the official `deploy/compose` Makefile workflow.
- Use `make all` for the normal deploy path.
- Use `make all-build` only when the user explicitly requests source builds.
- Do not use Vexa Lite, Helm, local platform runbooks, archived notes, or docs-site deployment pages as substitutes.
- Do not add helper scripts or alternative deployment recipes.
- Do not print transcription tokens, admin tokens, database credentials, API keys, or any secret values.
- If the official docs are missing, contradictory, or do not cover the requested environment, stop and report the exact documentation gap.

## Deploy Flow

1. Confirm the target checkout is the upstream `Vexa-ai/vexa` repo or clone it using the official deploy docs.
2. Enter `deploy/compose`.
3. Run `make all` for prebuilt images, or `make all-build` only when source builds are explicitly requested.
4. Let the official flow create/patch `.env`, start services, sync schema, create an API key, and verify connectivity.

## Completion Criteria

Treat the deploy as incomplete until the official verification path succeeds:

- `make all` or `make all-build` completes successfully.
- `make test` passes.
- `make test-transcription` passes when transcription verification is in scope.
- `make ps` shows the expected running services.

Use `make down && docker compose ps` when the user asks to stop or clean up the stack. Report URLs, checks run, current image tag when shown by official commands, and final pass/fail state. Redact secrets.
