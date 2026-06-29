#!/usr/bin/env bash
# =============================================================================
# carve/manifest.sh — the SINGLE SOURCE OF TRUTH for the open-core carve.
# Read by both seed.sh (one-time full-history seed) and sync.sh (incremental PR).
# Editing this file is how you change "what gets contributed" — nowhere else.
# =============================================================================

# Source monorepo + the source branch to carve from
export MONO="${MONO:-/home/dima/vexa-0.12}"
export SRC_BRANCH="${SRC_BRANCH:-0.12}"

# Published open-core repo (local clone) + its GitHub remote
export CARVE="${CARVE:-/home/dima/vexa-core}"
export CARVE_REMOTE="${CARVE_REMOTE:-https://github.com/Vexa-ai/vexa-core.git}"

# --- INCLUDE: the open-core allowlist (paths relative to MONO) ---------------
# core = runtime+meetings+agents+identity; deploy/compose = the self-host stack;
# clients/terminal = the reference workbench UI; docs/docs = Mintlify site.
export CARVE_INCLUDE=(
  core
  deploy/compose
  deploy/transcription
  clients/terminal
  docs/docs
  package.json
  pnpm-workspace.yaml
  pnpm-lock.yaml
  turbo.json
  tsconfig.base.json
  README.md
  architecture.calm.json
  .gitignore
  .dockerignore
)

# --- EXCLUDE: sub-paths purged even though under an INCLUDE root --------------
# ONLY human+AI-APPROVED removals live here (see carve/propose.sh → PROPOSAL.md).
# NOTE: eval/ dirs are NOT blanket-excluded — the deterministic counting-fixture
# system (core/meetings/eval/COUNTING-FIXTURES.md + counting_*.py + replay-fixture)
# is a reusable e2e asset that seeds downstream module fixtures. Individual
# personal-infra files are surfaced by propose.sh for per-file sign-off, not
# auto-dropped here.
export CARVE_EXCLUDE=(
  # commercial dashboard overlay inside compose (dashboard retired in favor of terminal)
  deploy/compose/docker-compose.dashboard.yml
  deploy/compose/bin/dashboard-harness.sh
  deploy/compose/tests/dashboard_surface.py
  # [approved A] personal-infra runbooks (ssh bbb / /home/dima rig) — no external value
  core/meetings/eval/O6-MEET-LEG.md
  core/meetings/eval/BASELINE.md
  core/meetings/eval/src/read-redis-transcript.mjs
  core/meetings/services/bot/eval/README.md
  core/meetings/services/bot/eval/RUNBOOK.md
  # [approved B] local-rig driver: reads /home/dima/.env.local + imports removed clients/slim
  core/meetings/eval/src/counting_replay.py
  # [approved C] integration tests for the removed clients/slim component (would fail in carve)
  core/agent/tests/test_cookbook_l2.py
  core/agent/tests/test_cookbook_l3.py
)

# --- FLAG: patterns that mark a candidate file as "needs human+AI review" -----
# propose.sh classifies any candidate matching these as FLAG (sanitize-or-drop),
# never auto-removing. The human approves: sanitize (keep) or add to CARVE_EXCLUDE.
# High-signal only: personal paths/hosts, internal hostnames, refs to dropped dirs.
# Deliberately NOT flagging 127.0.0.1/0.0.0.0/169.254/private-CIDR bind+test IPs (legit).
export CARVE_FLAG_PATTERNS='/home/dima|ssh bbb|\.env\.local|transcription\.vexa\.ai|clients/(slim|dashboard)'

# --- Identity normalization (placeholder authors → real identities) ----------
export CARVE_MAILMAP="$MONO/carve/mailmap.txt"

# --- Carve-owned override files (copied over the mono's after materialize) ---
# Each line: "<override-file-under-carve/overrides/>  <dest-path-in-carve>"
export CARVE_OVERRIDES=(
  "Makefile:Makefile"          # compose-only entrypoint (mono's references removed deploy/lite)
)

# --- Deterministic transforms applied after materialize ----------------------
# Hook: carve/transform.sh runs in the carve working dir each seed/sync.
export CARVE_TRANSFORM="$MONO/carve/transform.sh"

# docs/docs is currently UNTRACKED in the mono (staged on disk only); until it is
# committed upstream, seed/sync source it from the working tree. Flip to 0 once
# docs/docs is committed on $SRC_BRANCH so it flows through git history normally.
export CARVE_DOCS_FROM_WORKTREE="${CARVE_DOCS_FROM_WORKTREE:-1}"
