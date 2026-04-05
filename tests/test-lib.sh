#!/usr/bin/env bash
# test-lib.sh — Shared logging for test pipeline scripts
# Source this at the top of every test script:
#   TEST_ID="test/bot-lifecycle"
#   source "$SCRIPT_DIR/test-lib.sh"
#
# Log format:
#   YYYY-MM-DD HH:MM:SS — EVENT — procedure: message [duration] [parent]
#
# Events:
#   START    — why this procedure is running (args, trigger, context)
#   PASS     — step/procedure passed (evidence, duration)
#   FAIL     — step/procedure failed (what went wrong, duration)
#   FINDING  — something surprising discovered
#   QUESTION — unknown, needs investigation later
#   FIX      — agent fixed something in the actual software
#   SKIP     — step skipped (why: missing prereq, not applicable)

_STEPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LOG_FILE="$_STEPS_DIR/test-log.md"
_START_TIME=""
_PARENT_ID="${PARENT_TEST_ID:-}"

# Create log file if missing
if [ ! -f "$_LOG_FILE" ]; then
  mkdir -p "$(dirname "$_LOG_FILE")"
  cat > "$_LOG_FILE" << 'HEADER'
# Test Pipeline Log

Append-only. Read by `0-full` to understand current state across sessions.

Format: `TIME — EVENT — procedure: message [duration] [parent:X]`

## Entries

HEADER
fi

# Core log function
log() {
  local event="$1"
  shift
  local msg="$*"
  local ts=$(date '+%Y-%m-%d %H:%M:%S')
  local extra=""

  # Add duration for PASS/FAIL (if we have a start time)
  if [ -n "$_START_TIME" ] && { [ "$event" = "PASS" ] || [ "$event" = "FAIL" ]; }; then
    local now=$(date +%s)
    local elapsed=$((now - _START_TIME))
    extra=" [${elapsed}s]"
  fi

  # Add parent context if this was called by another procedure
  if [ -n "$_PARENT_ID" ]; then
    extra="$extra [parent:$_PARENT_ID]"
  fi

  local entry="$ts — $event — ${TEST_ID:-unknown}: $msg$extra"
  echo "$entry" >> "$_LOG_FILE"
  echo "$entry" >&2
}

# START — must include WHY: what triggered this, with what args
log_start() {
  _START_TIME=$(date +%s)
  log START "$*"
}

# PASS — what passed, with evidence
log_pass() { log PASS "$*"; }

# FAIL — what failed, exits script
log_fail() { log FAIL "$*"; exit 1; }

# FINDING — something surprising (append-only knowledge)
log_finding() { log FINDING "$*"; }

# QUESTION — needs investigation, not blocking
log_question() { log QUESTION "$*"; }

# FIX — agent changed the actual software to resolve a failure
log_fix() { log FIX "$*"; }

# SKIP — why this step was skipped
log_skip() { log SKIP "$*"; }
