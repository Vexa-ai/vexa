#!/usr/bin/env bash
set -euo pipefail

# build-clean.sh — Build a clean branch with logical commits grouped by feature
#
# Takes the full diff from feature/agentic-runtime vs origin/main,
# splits it into ~15 logical commits with proper attribution,
# and strips dev artifacts per .cleanignore.
#
# Usage:
#   ./scripts/build-clean.sh                    # rebuild clean branch
#   ./scripts/build-clean.sh --push             # rebuild and push

SOURCE_BRANCH="feature/agentic-runtime"
PUSH=false

for arg in "$@"; do
  case "$arg" in
    --push) PUSH=true ;;
  esac
done

REPO_ROOT="$(git rev-parse --show-toplevel)"
CLEANIGNORE="$REPO_ROOT/.cleanignore"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Checkout files matching patterns from source branch, stage, and commit
# Usage: commit_group "author" "message" pattern1 pattern2 ...
commit_group() {
  local author="$1"; shift
  local message="$1"; shift
  local patterns=("$@")
  local staged=false

  for pattern in "${patterns[@]}"; do
    # Use git diff to find changed files matching pattern
    local files
    files=$(git diff --name-only "origin/main...$SOURCE_BRANCH" -- "$pattern" 2>/dev/null || true)
    if [ -n "$files" ]; then
      echo "$files" | while IFS= read -r f; do
        if git show "$SOURCE_BRANCH:$f" >/dev/null 2>&1; then
          mkdir -p "$(dirname "$f")"
          git show "$SOURCE_BRANCH:$f" > "$f" 2>/dev/null || true
          git add "$f" 2>/dev/null || git add -f "$f" 2>/dev/null || true
        fi
      done
      staged=true
    fi
  done

  if ! git diff --cached --quiet 2>/dev/null; then
    git commit --author="$author" -m "$message" >/dev/null 2>&1
    echo "  ✓ $(echo "$message" | head -1)"
  fi
}

# Commit ALL remaining changed files
commit_remaining() {
  local author="$1"; shift
  local message="$1"; shift

  # Get all files from source branch
  git checkout "$SOURCE_BRANCH" -- . 2>/dev/null || true
  git add -A 2>/dev/null || true

  # Force-add files that .gitignore blocks
  local blocked
  blocked=$(git diff --name-only "$SOURCE_BRANCH" -- . 2>/dev/null | head -200)
  if [ -n "$blocked" ]; then
    echo "$blocked" | xargs git add -f 2>/dev/null || true
  fi

  if ! git diff --cached --quiet 2>/dev/null; then
    git commit --author="$author" -m "$message" >/dev/null 2>&1
    echo "  ✓ $(echo "$message" | head -1)"
  fi
}

# Strip .cleanignore paths
strip_artifacts() {
  if [ ! -f "$CLEANIGNORE" ]; then return; fi

  local removed=0
  while IFS= read -r line; do
    line="${line%%#*}"
    line="${line// /}"
    [ -z "$line" ] && continue
    local matches
    matches=$(git ls-files --cached "$line" 2>/dev/null || true)
    if [ -n "$matches" ]; then
      echo "$matches" | xargs git rm -r --cached --quiet --force 2>/dev/null || true
      echo "$matches" | xargs rm -rf 2>/dev/null || true
      removed=$((removed + $(echo "$matches" | wc -l)))
    fi
  done < "$CLEANIGNORE"

  if ! git diff --cached --quiet 2>/dev/null; then
    git commit -m "chore: strip $removed dev artifacts via .cleanignore" >/dev/null 2>&1
    echo "  ✓ Stripped $removed dev-only files"
  fi
}

# ---------------------------------------------------------------------------
# Authors
# ---------------------------------------------------------------------------

DIMA="DmitriyG228 <2280905@gmail.com>"
AGROGOV="agrogov <agrogov@users.noreply.github.com>"
JACOB="Jacob Schooley <jacob@schooley.com>"

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "=== Building clean branch from $SOURCE_BRANCH ==="

# Stash uncommitted work
STASHED=false
if ! git diff --quiet || ! git diff --cached --quiet; then
  git stash push -m "build-clean: auto-stash"
  STASHED=true
fi

git fetch origin main --quiet 2>/dev/null || true

# Delete and recreate clean branch from main
git branch -D clean 2>/dev/null || true
git checkout -b clean origin/main
echo ""
echo "Building commits..."

# 1. Database models, migrations, schemas
commit_group "$DIMA" \
  "feat: database models, migrations, and schemas

Shared models library: TranscriptionSegment, Recording, MediaFile,
CalendarEvent, WebhookUrl, WebhookDelivery. Token scoping, scheduler,
security headers. Alembic migrations." \
  "libs/shared-models/"

# 2. API services
commit_group "$DIMA" \
  "feat: API services (gateway, admin, agent, runtime)

API gateway with auth middleware and CORS. Admin API for user/bot/meeting
management. Agent API for chat sessions, scheduling, workspaces.
Runtime API for container orchestration and profiles." \
  "services/api-gateway/" "services/admin-api/" "services/agent-api/" "services/runtime-api/"

# 3. Meeting API and orchestrators
commit_group "$DIMA" \
  "feat: meeting API with Docker, Kubernetes, and process orchestrators

Bot lifecycle management, concurrent launch control, meeting state machine.
Post-meeting hooks, webhook delivery, status callbacks.
Pluggable orchestrators: Docker, Kubernetes, process-based." \
  "services/meeting-api/"

# 4. Transcription pipeline
commit_group "$DIMA" \
  "feat: transcription pipeline (collector, service, WhisperLive)

Transcription collector with streaming consumer, DB writer, filters.
Transcription service with WhisperLive integration, remote transcriber.
Hallucination filtering (en, es, pt, ru). Speaker mapping and identity." \
  "services/transcription-service/" "services/WhisperLive/"

# 5. Vexa bot — platforms
commit_group "$DIMA" \
  "feat: multi-platform meeting bot (Google Meet, MS Teams, Zoom web)

Browser-based bot with platform-specific strategies for joining, recording,
admission handling, speaker detection, and removal detection.
Shared meeting flow, escalation, and browser session management.
Audio capture, microphone streaming, screen content, VAD." \
  "services/vexa-bot/"

# 6. TTS service
commit_group "$DIMA" \
  "feat: TTS service for voice agent meeting participation

Text-to-speech service with OpenAI integration. TTS playback service
in the bot for real-time meeting participation." \
  "services/tts-service/"

# 7. Dashboard — agrogov contributions (PRs #2-6)
commit_group "$AGROGOV" \
  "feat(dashboard): Azure AD SSO, Azure OpenAI, base path, runtime config, caching

Microsoft Entra ID (Azure AD) SSO authentication (PR #3).
Azure OpenAI as AI provider for chat endpoint (PR #4).
Configurable base path for sub-path deployments (PR #5).
Runtime config for decision listener URL (PR #6).
Transcript proxy caching fix (PR #2)." \
  "services/dashboard/next.config.ts" \
  "services/dashboard/src/app/api/ai/" \
  "services/dashboard/src/app/api/auth/" \
  "services/dashboard/src/app/api/config/" \
  "services/dashboard/src/app/api/health/" \
  "services/dashboard/src/app/api/vexa/" \
  "services/dashboard/src/app/auth/" \
  "services/dashboard/src/app/login/" \
  "services/dashboard/src/app/mcp/" \
  "services/dashboard/src/app/meetings/page.tsx" \
  "services/dashboard/src/app/profile/" \
  "services/dashboard/src/app/settings/" \
  "services/dashboard/src/app/tracker/" \
  "services/dashboard/src/components/admin/admin-guard.tsx" \
  "services/dashboard/src/components/ai/" \
  "services/dashboard/src/components/decisions/" \
  "services/dashboard/src/components/join/join-modal.tsx" \
  "services/dashboard/src/components/layout/sidebar.tsx" \
  "services/dashboard/src/components/mcp/" \
  "services/dashboard/src/components/meetings/browser-session-view.tsx" \
  "services/dashboard/src/components/ui/logo.tsx" \
  "services/dashboard/src/hooks/" \
  "services/dashboard/src/stores/admin-auth-store.ts" \
  "services/dashboard/src/stores/auth-store.ts" \
  "services/dashboard/src/stores/webhook-store.ts"

# 8. Dashboard — jbschooley contributions (PRs #7-10, #181)
commit_group "$JACOB" \
  "feat: video recording, timezone fixes, video playback (PRs #7-10, #181)

Video recording playback component (PR #10).
Timezone handling fixes across admin and transcript views (PR #7).
Zoom web client and video recording (PR #181)." \
  "services/dashboard/src/app/admin/" \
  "services/dashboard/src/components/recording/" \
  "services/dashboard/src/components/transcript/transcript-segment.tsx" \
  "services/dashboard/src/lib/export.ts"

# 9. Dashboard — remaining
commit_group "$DIMA" \
  "feat(dashboard): admin panel, agent chat, meeting views, docs, webhooks

Full dashboard application: meeting list, transcript viewer, admin panel,
agent chat integration, webhook management, workspace editor, docs pages.
Auth flow, theme support, notification system." \
  "services/dashboard/"

# 10. MCP server
commit_group "$DIMA" \
  "feat: MCP server for AI tool integration

Model Context Protocol server exposing Vexa APIs as tools.
Meeting URL parsing, transcript access, recording tools." \
  "services/mcp/"

# 11. Calendar and Telegram — NO-SHIP for 0.10, skipped
# commit_group "$DIMA" \
#   "feat: calendar service and Telegram bot
#
# Google Calendar sync with auto-scheduling for meetings.
# Telegram bot for mobile meeting management." \
#   "services/calendar-service/" "services/telegram-bot/"

# 12. Transcript rendering library
commit_group "$DIMA" \
  "feat: transcript rendering library

TypeScript library for transcript dedup, grouping, timestamps.
Packaged for use in dashboard." \
  "services/transcript-rendering/"

# 13. Deployment (Helm, compose, lite)
commit_group "$DIMA" \
  "feat: deployment configs (Helm charts, docker-compose, Vexa Lite)

Helm charts for full and lite deployments. Docker compose for local dev.
Vexa Lite single-container mode. Deploy scripts, env examples." \
  "deploy/" "docker-compose.override.yml"

# 14. Agent container
commit_group "$DIMA" \
  "feat: agent container with Claude Code and Vexa CLI

Docker image packaging Claude Code + Vexa CLI for agentic runtime.
System prompt, workspace sync, container spawning, browser connections." \
  "containers/"

# 15. Test infrastructure
commit_group "$DIMA" \
  "test: unit, integration, smoke, load, and audit test infrastructure

Per-service unit tests. Integration tests for bot interaction, websockets.
Smoke tests for full stack. Load tests for transcription service.
Security, architecture, config, and staleness audits." \
  "tests/" "test_data/"

# 16. Docs
commit_group "$DIMA" \
  "docs: comprehensive API, platform, and deployment documentation

Mintlify-powered docs: API reference (bots, meetings, transcripts,
recordings), platform guides (Zoom, Meet, Teams), deployment,
webhooks, websockets, security, troubleshooting." \
  "docs/"

# 17. Features (design docs, specs, research — minus agent artifacts)
commit_group "$DIMA" \
  "docs: feature specifications and design documents

Feature READMEs, design docs, env examples, test plans, and Makefiles
for: realtime transcription, post-meeting, speaking bot, calendar,
webhooks, token scoping, MCP, multi-platform, remote browser, scheduler." \
  "features/"

# 18. Everything else (top-level files, scripts, infra, etc.)
commit_remaining "$DIMA" \
  "chore: project config, scripts, CI, and infrastructure

README, CONTRIBUTING, TESTING, SECURITY, Makefile, VERSION, .gitignore,
.gitmodules. Sync scripts, Mintlify sync. Infrastructure docs."

# 19. Strip dev artifacts
echo ""
echo "Stripping dev artifacts..."
strip_artifacts

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "=== Clean branch built ==="
TOTAL=$(git log --oneline origin/main..clean | wc -l)
echo "Total commits: $TOTAL"
echo ""
git log --oneline origin/main..clean
echo ""

# Verify attribution
echo "=== Author attribution ==="
git log --format="%an <%ae>" origin/main..clean | sort | uniq -c | sort -rn

if [ "$PUSH" = true ]; then
  echo ""
  echo "Pushing clean to origin..."
  git push origin clean --force-with-lease
fi

# Return to source branch
git checkout "$SOURCE_BRANCH"

if [ "$STASHED" = true ]; then
  git stash pop
fi

echo ""
echo "Done. You're back on $SOURCE_BRANCH."
