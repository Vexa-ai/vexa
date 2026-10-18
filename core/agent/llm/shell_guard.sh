#!/bin/bash
# shell_guard.sh — every shell command the model runs goes through this (CLAUDE_CODE_SHELL_PREFIX,
# set by llm/claude_code.shell_guard_env). The CLI calls it as `shell_guard.sh '<command string>'`.
#
# Two rules (live app.dev 2026-10-15: a grep over the CLI's own transcripts ran 14+ minutes, the
# CLI's timeout only moved it to the background, and the conversation was held until an operator
# killed the pid):
#   1. A WALL-CLOCK LIMIT. The command runs under `timeout`, which kills its whole process group
#      (a pipeline's grep included) at VEXA_SHELL_TIMEOUT_SEC (policy `shell_timeout_sec`, default
#      120), and the model is told in words that it timed out.
#   2. THE AGENT'S OWN TRANSCRIPTS ARE NOT READ. A command that names the CLI's conversation files is
#      refused before it runs, with the reason.
cmd=${1-}
limit=${VEXA_SHELL_TIMEOUT_SEC:-120}
case $limit in '' | *[!0-9]*) limit=120 ;; esac
[ "$limit" -ge 1 ] 2>/dev/null || limit=120

case $cmd in
  *.claude/projects* | *.vexa-session* | *claude/projects/-workspaces*)
    printf '%s\n' "vexa: refused - this command reads the agent's own conversation transcripts (.claude/projects)." \
      "They hold every past turn, tool output and credential that passed through a chat, so the shell does not read them." \
      "Ask the person, or search the workspace's pages instead." >&2
    exit 126 ;;
esac

# Only the CLI's own tool commands are timed (they `eval` the command and record `pwd -P` after it);
# anything else the CLI starts through the prefix runs exactly as it would without it.
case $cmd in
  *eval\ *pwd\ -P*) ;;
  *) exec /bin/bash -c "$cmd" ;;
esac

start=$SECONDS
timeout --kill-after=5s "${limit}s" /bin/bash -c "$cmd"
rc=$?
if { [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; } && [ $((SECONDS - start)) -ge "$limit" ]; then
  printf '\n%s\n' "vexa: timed out - this command was stopped after ${limit}s, the shell tool's time limit (policy shell_timeout_sec). Nothing it started is still running. Narrow it (a smaller path, a simpler pattern, head or grep -m to stop early) and run it again." >&2
  exit 124
fi
exit "$rc"
