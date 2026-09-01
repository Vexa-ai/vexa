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
FL="${VEXA_FLOWS_SRC:-/home/dima/dev/vexa-flows1315/core/flows}"
PUBLIC_MCP_URL="${VEXA_PUBLIC_MCP_URL:-https://rig.dev.vexa.ai/mcp}"
UI_URL="${VEXA_UI_URL:-https://app.dev.vexa.ai}"

# The server this script runs is the file NEXT TO IT — the repo copy when run from the repo, the
# ~/.storm symlink to that same file when run from there. Neither spelling names a home directory.
RIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CTL="$RIG_DIR/vexa_control_mcp.py"

V=$FL/.venv/bin
LOG=/tmp/storm-logs
mkdir -p "$LOG"

up_port() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

start_ctl() {
  tmux kill-session -t stormctl 2>/dev/null
  tmux new-session -d -s stormctl -c /tmp \
    "VEXA_FLOWS_SRC=\"$FL\" VEXA_PUBLIC_MCP_URL=\"$PUBLIC_MCP_URL\" VEXA_UI_URL=\"$UI_URL\" \
     VEXA_MCP_DELEGATION_SECRET=\"\$(cat \"\$HOME/.storm/delegation-secret\" 2>/dev/null)\" \
     $V/python -u \"$CTL\" 2>&1 | tee $LOG/control-mcp.log"
}
start_api() {
  tmux kill-session -t stormapi 2>/dev/null
  tmux new-session -d -s stormapi -c "$FL" \
    "PYTHONPATH=$FL/src VEXA_FLOWS_DB_URL=$(cat "$HOME/.storm/dburl") \
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
  printf "  %-18s %s\n" "flows-worker"    "$(pgrep -f flows_worker >/dev/null && echo UP || echo DOWN)"
  printf "  %-18s %s\n" "dogfood gateway" "$(curl -s -m 4 localhost:18456/health >/dev/null && echo UP || echo DOWN)"
  echo "  tmux: $(tmux ls 2>/dev/null | grep -c storm) storm sessions"
}

case "${1:-status}" in
  status) echo "storm rig:"; status ;;
  config) echo "flows src:  $FL"; echo "server:     $CTL"; echo "publishes:  $PUBLIC_MCP_URL"; echo "terminal:   $UI_URL" ;;
  up)
    start_mailpit
    up_port 18200 || start_api
    pgrep -f flows_worker >/dev/null || start_worker
    up_port 18310 || start_ctl
    sleep 8; echo "storm rig after up:"; status ;;
  restart)
    start_mailpit; start_api; start_worker; start_ctl
    sleep 10; echo "storm rig restarted:"; status ;;
  down)
    for s in stormctl stormapi stormworker stormflows; do tmux kill-session -t $s 2>/dev/null; done
    pkill -f vexa_control_mcp; pkill -f flows_worker; pkill -f "uvicorn flows"
    docker rm -f storm-mailpit >/dev/null 2>&1
    echo "storm rig down (dogfood stack untouched)" ;;
  *) echo "usage: rig.sh status|config|up|restart|down"; exit 1 ;;
esac
