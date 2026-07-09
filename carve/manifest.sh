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
  clients/slim
  docs/docs
  package.json
  pnpm-workspace.yaml
  pnpm-lock.yaml
  turbo.json
  tsconfig.base.json
  README.md
  SECURITY.md
  security-insights.yml
  security
  architecture.calm.json
  calm
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
  # [approved B] local-rig driver: reads /home/dima/.env.local (personal rig path) — personal-infra, no external value
  core/meetings/eval/src/counting_replay.py
)

# --- FLAG: patterns that mark a candidate file as "needs human+AI review" -----
# propose.sh classifies any candidate matching these as FLAG (sanitize-or-drop),
# never auto-removing. The human approves: sanitize (keep) or add to CARVE_EXCLUDE.
# High-signal only: personal paths/hosts, internal hostnames, refs to dropped dirs.
# Deliberately NOT flagging 127.0.0.1/0.0.0.0/169.254/private-CIDR bind+test IPs (legit).
export CARVE_FLAG_PATTERNS='/home/dima|ssh bbb|\.env\.local|transcription\.vexa\.ai|clients/dashboard'

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

# docs/docs is fully git-tracked in the mono — docs flow through git history like
# everything else (authorship preserved; uncommitted local edits can no longer leak
# into a carve). The worktree mode (=1) remains only as a manual escape hatch.
export CARVE_DOCS_FROM_WORKTREE="${CARVE_DOCS_FROM_WORKTREE:-0}"
