#!/usr/bin/env bash
# rig — health-check and self-heal the storm hot loop on bbb.
#   rig.sh status   what is up
#   rig.sh up       start anything that is down (idempotent)
#   rig.sh down     stop only what this rig owns
# Touches nothing in the vexa-dogfood stack; that belongs to another session.
set -u
FL=/home/dima/dev/vexa-flows1315/core/flows
V=$FL/.venv/bin
LOG=/tmp/storm-logs
mkdir -p "$LOG"

up_port() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

start_ctl() {
  tmux kill-session -t stormctl 2>/dev/null
  tmux new-session -d -s stormctl -c /tmp \
    "VEXA_MCP_DELEGATION_SECRET=\"\$(cat \"\$HOME/.storm/delegation-secret\" 2>/dev/null)\" \
     $V/python -u /home/dima/.storm/vexa_control_mcp.py 2>&1 | tee $LOG/control-mcp.log"
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
    "bash /home/dima/.storm/flows-up.sh; sleep infinity"
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
  *) echo "usage: rig.sh status|up|restart|down"; exit 1 ;;
esac
