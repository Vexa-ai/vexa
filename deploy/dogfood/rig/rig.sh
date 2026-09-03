#!/usr/bin/env bash
# rig — health-check and self-heal the storm hot loop on bbb.
#   rig.sh status   what is up
#   rig.sh up       start anything that is down (idempotent)
#   rig.sh down     stop only what this rig owns
# Touches nothing in the vexa-dogfood stack; that belongs to another session.
set -u

# ── everything this rig has to be TOLD, in one block ──────────────────────────────────────────
# Each is `${VAR:-default}`, and each default is what this host has always used: an unconfigured
# rig starts exactly as it did before, and a rig on any other host is four exports rather than an
# edit to this file. That is the whole point — the rig used to name one developer's home directory
# from inside the server source, where no deployment could reach it.
#
#   VEXA_FLOWS_SRC       the flows checkout's core/flows. The venv, the flows API and the worker
#                        below all run out of it, and fact_emit imports the engine from its src/.
#                        Absent, the server still starts and fact_emit alone reports unavailable.
#   VEXA_PUBLIC_MCP_URL  the name the server PUBLISHES. It drives the sign-in links, the /connect
#                        bootstrap AND the transport host guard — a server that publishes one name
#                        while admitting another refuses its own users.
#   VEXA_UI_URL          the terminal that deeplink() sends people to. Left unset the server falls
#                        back to a localhost port nothing serves, and every minted link is dead on
#                        arrival while looking perfectly well-formed.
#   VEXA_MCP_DELEGATION_SECRET  the HMAC key agent-api mints delegated tokens with. Read from
#                        $HOME/.storm/delegation-secret at start; never echoed, never defaulted.
#   INTERNAL_API_SECRET  the INTERNAL TIER. Read from $HOME/.storm/internal-secret at start; must
#                        equal agent-api's own. The control server and flows-api both REFUSE TO
#                        START without it (F95) — deliberately, because without it the rig cannot
#                        authenticate as a service and nothing about that failure is visible.
# ONE LINE (2026-09-02): the flows checkout is the LINE worktree. This default is not
# cosmetic — start_worker passes VEXA_FLOWS_SRC="$FL" into flows-up.sh, which OVERRIDES
# flows-up.sh's own default, so a later `rig.sh restart` with the old value here would
# silently move the running engine back to the pre-merge lineage and quietly undo the
# attendee follow-up, the note-date fix and the provenance lines. Previous value:
#   FL="${VEXA_FLOWS_SRC:-/home/dima/dev/vexa-flows1315/core/flows}"
FL="${VEXA_FLOWS_SRC:-/home/dima/dev/wt-line/core/flows}"
# THE FLOWS VENV, for the flows processes only. It still lives in the old checkout: same
# dependencies, and a worktree has none of its own. Source and interpreter are different questions.
# The CONTROL SERVER no longer runs out of it — see RIG_VENV below.
VENV_DIR="${VEXA_FLOWS_VENV:-/home/dima/dev/vexa-flows1315/core/flows}"
PUBLIC_MCP_URL="${VEXA_PUBLIC_MCP_URL:-https://rig.dev.vexa.ai/mcp}"
UI_URL="${VEXA_UI_URL:-https://app.dev.vexa.ai}"

# The server this script runs is the file NEXT TO IT — the repo copy when run from the repo, the
# ~/.storm symlink to that same file when run from there. Neither spelling names a home directory.
RIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CTL="$RIG_DIR/vexa_control_mcp.py"

V=$VENV_DIR/.venv/bin

# ── THE RIG'S OWN VENV, AND ITS OWN PIDFILES ─────────────────────────────────────────────────
# 2026-09-03, live, 2.5 minutes down: the control server was started with $V — the venv of
# `~/dev/vexa-flows1315`, a stale checkout that three other things also run out of. A module the
# rig's import chain reached was not installed there, so the server would not start, and the fix
# had to be an install into a venv nobody owns. The rig had no dependency declaration and no
# interpreter of its own; it borrowed whichever was nearest.
#
# It has both now. `deploy/dogfood/rig/pyproject.toml` says what it needs and this builds a venv
# from exactly that, next to the source, so the flows venv can be rebuilt, moved or deleted
# without taking the rig with it.
RIG_VENV="${VEXA_RIG_VENV:-$RIG_DIR/.venv}"
RV="$RIG_VENV/bin"
UV="${UV:-uv}"

ensure_venv() {
  [ -x "$RV/python" ] && return 0
  command -v "$UV" >/dev/null 2>&1 || {
    echo "rig: no venv at $RIG_VENV and no uv to build one — install uv, or set VEXA_RIG_VENV" >&2
    return 1
  }
  echo "rig: building $RIG_VENV from $RIG_DIR/pyproject.toml…"
  "$UV" venv "$RIG_VENV" >/dev/null 2>&1 || return 1
  # Runtime only. The dev group is pytest, which the rig does not need to serve.
  "$UV" pip install --python "$RV/python" -r "$RIG_DIR/pyproject.toml" >/dev/null || {
    echo "rig: could not install the rig's dependencies into $RIG_VENV" >&2
    return 1
  }
}

# STOP BY PID, NEVER BY PATTERN. `pkill -f vexa_control_mcp` matches any command line containing
# that string, and on 2026-09-03 that included stage-1's own ssh session: stopping the rig killed
# the session doing the stopping. `-f` is a substring search over other people's arguments, not
# process identity.
RUN_DIR="${VEXA_RIG_RUN_DIR:-$HOME/.storm/run}"
CTL_PIDFILE="$RUN_DIR/control-mcp.pid"

# What a recorded pid's command line must look like before we signal it. The pidfile is the
# identity; this is only the guard against a pid that has been recycled since it was written —
# between a crash and a `down`, that number can belong to anybody.
CTL_PAT="vexa_control_mcp"

stop_by_pidfile() {  # stop_by_pidfile <file> [pattern]
  local f="${1:-}" pat="${2:-$CTL_PAT}" pid cmd
  [ -n "$f" ] && [ -r "$f" ] || return 0
  pid="$(head -1 "$f" 2>/dev/null | tr -dc '0-9')"
  rm -f "$f"
  [ -n "$pid" ] || return 0
  kill -0 "$pid" 2>/dev/null || return 0          # already gone; the file was stale
  cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
  case "$cmd" in
    *"$pat"*) : ;;
    *) echo "rig: pid $pid is no longer the rig ($cmd) — not signalling it"; return 0 ;;
  esac
  kill "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 0.2
  done
  kill -9 "$pid" 2>/dev/null
  return 0
}

# Sourced as a library (the tests drive these functions directly) — define and stop here.
if [ -n "${RIG_SH_LIB:-}" ]; then return 0 2>/dev/null || exit 0; fi

LOG=/tmp/storm-logs
mkdir -p "$LOG" "$RUN_DIR"

up_port() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

# Is a process of THIS LANE running? `pgrep -f flows_worker` was the old test, and it matches ANY
# flows worker on the host — including another lane's, running from a different checkout. With two
# lanes up, `up` decided this lane's worker was already running, skipped it, and left an
# eight-minute-stale process serving a merge it had never loaded. A whole revolution of the DNA
# replay was scored against pre-merge code before anyone noticed, because nothing was down and
# nothing errored.
#
# So a lane is identified by the checkout its processes actually point at: PYTHONPATH in the
# process's own environ. Same host, two lanes, no ambiguity.
lane_pids() {  # lane_pids <pattern>
  local pat="$1" pid src
  for pid in $(pgrep -f "$pat" 2>/dev/null); do
    [ -r "/proc/$pid/environ" ] || continue   # it exited between pgrep and here
    src=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^PYTHONPATH=//p' | head -1)
    case "$src" in "$FL"/src*) echo "$pid" ;; esac
  done
}
lane_up() { [ -n "$(lane_pids "$1")" ]; }

start_ctl() {
  ensure_venv || { echo "rig: refusing to start the control server without its own venv" >&2; return 1; }
  stop_by_pidfile "$CTL_PIDFILE"
  tmux kill-session -t stormctl 2>/dev/null
  # `echo $! > pidfile` INSIDE the session, so the pid recorded is the python process itself and
  # not tmux's. The `wait` keeps the pane alive and the exit status honest.
  tmux new-session -d -s stormctl -c /tmp \
    "VEXA_FLOWS_SRC=\"$FL\" VEXA_PUBLIC_MCP_URL=\"$PUBLIC_MCP_URL\" VEXA_UI_URL=\"$UI_URL\" \
     VEXA_MCP_DELEGATION_SECRET=\"\$(cat \"\$HOME/.storm/delegation-secret\" 2>/dev/null)\" \
     INTERNAL_API_SECRET=\"\$(cat \"\$HOME/.storm/internal-secret\" 2>/dev/null)\" \
     $RV/python -u \"$CTL\" > >(tee $LOG/control-mcp.log) 2>&1 & \
     echo \$! > \"$CTL_PIDFILE\"; wait"
}
start_api() {
  tmux kill-session -t stormapi 2>/dev/null
  tmux new-session -d -s stormapi -c "$FL" \
    "PYTHONPATH=$FL/src VEXA_FLOWS_DB_URL=$(cat "$HOME/.storm/dburl") \
     INTERNAL_API_SECRET=\"\$(cat \"\$HOME/.storm/internal-secret\" 2>/dev/null)\" \
     $V/python -m uvicorn flows_integrations.flows_api:app --host 127.0.0.1 --port 18200 \
     2>&1 | tee $LOG/flows-api.log"
}
start_worker() {
  tmux kill-session -t stormworker 2>/dev/null
  tmux new-session -d -s stormworker -c "$FL" \
    "VEXA_FLOWS_SRC=\"$FL\" bash \"$RIG_DIR/flows-up.sh\"; sleep infinity"
}
start_mailpit() {
  docker ps --format '{{.Names}}' | grep -q '^storm-mailpit$' || \
    docker run -d --name storm-mailpit --network vexa-dogfood_vexa \
      -p 127.0.0.1:8025:8025 -p 127.0.0.1:1025:1025 axllent/mailpit:latest >/dev/null
}

status() {
  printf "  %-18s %s\n" "mailpit :8025"  "$(up_port 8025  && echo UP || echo DOWN)"
  printf "  %-18s %s\n" "flows-api :18200" "$(up_port 18200 && echo UP || echo DOWN)"
  printf "  %-18s %s\n" "control-mcp :18310" "$(up_port 18310 && echo UP || echo DOWN)"
  printf "  %-18s %s\n" "flows-worker"    "$(lane_up 'flows_worker' && echo "UP ($(lane_pids 'flows_worker' | tr '\n' ' '))" || echo DOWN)"
  printf "  %-18s %s\n" "lane checkout"   "$FL @ $(git -C "$FL" rev-parse --short HEAD 2>/dev/null || echo '?')"
  printf "  %-18s %s\n" "rig venv"        "$([ -x "$RV/python" ] && echo "$RIG_VENV" || echo "ABSENT (rig.sh up builds it)")"
  printf "  %-18s %s\n" "dogfood gateway" "$(curl -s -m 4 localhost:18456/health >/dev/null && echo UP || echo DOWN)"
  echo "  tmux: $(tmux ls 2>/dev/null | grep -c storm) storm sessions"
}

case "${1:-status}" in
  status) echo "storm rig:"; status ;;
  config) echo "flows src:  $FL"; echo "server:     $CTL"; echo "rig venv:   $RIG_VENV"
          echo "pidfile:    $CTL_PIDFILE"; echo "publishes:  $PUBLIC_MCP_URL"; echo "terminal:   $UI_URL" ;;
  up)
    start_mailpit
    lane_up 'flows_integrations.flows_api' || start_api
    lane_up 'flows_worker' || start_worker
    up_port 18310 || start_ctl
    sleep 8; echo "storm rig after up:"; status ;;
  restart)
    start_mailpit; start_api; start_worker; start_ctl
    sleep 10; echo "storm rig restarted:"; status ;;
  down)
    # The control server by PIDFILE; the flows processes by LANE (their PYTHONPATH names the
    # checkout, which is the identity `lane_pids` was already written for). Neither is a pattern
    # match against every command line on the host.
    stop_by_pidfile "$CTL_PIDFILE"
    for s in stormctl stormapi stormworker stormflows; do tmux kill-session -t $s 2>/dev/null; done
    for pid in $(lane_pids 'flows_worker') $(lane_pids 'flows_integrations.flows_api'); do
      kill "$pid" 2>/dev/null
    done
    docker rm -f storm-mailpit >/dev/null 2>&1
    echo "storm rig down (dogfood stack untouched)" ;;
  *) echo "usage: rig.sh status|config|up|restart|down"; exit 1 ;;
esac
