#!/usr/bin/env bash
# Bring up the flows half of the storm hot loop on bbb.
# Processes (not containers) so edits in the worktree are live on restart.
set -u
# Same VEXA_FLOWS_SRC the rig and the control server read — one name for the flows checkout,
# defaulted to what this host has always used so an unconfigured run is unchanged.
FL="${VEXA_FLOWS_SRC:-/home/dima/dev/vexa-flows1315/core/flows}"
LOG=/tmp/storm-logs
mkdir -p "$LOG"

export PYTHONPATH="$FL/src"
export VEXA_FLOWS_DB_URL="$(cat "$HOME/.storm/dburl")"
export VEXA_FLOWS_GATEWAY_URL="http://localhost:18456"
export VEXA_FLOWS_AGENT_API_URL="http://localhost:18500"
export VEXA_FLOWS_ADMIN_API_URL="http://localhost:18457"
export VEXA_FLOWS_ADMIN_KEY="$(docker inspect vexa-dogfood-admin-api-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ADMIN_API_TOKEN=' | cut -d= -f2)"

# the mail double — nothing can reach a real mailbox from this loop
export VEXA_MAIL_ADDR="vexa@storm.test"
export VEXA_MAIL_SMTP_HOST=127.0.0.1
export VEXA_MAIL_SMTP_PORT=1025
export VEXA_MAIL_SMTP_MODE=plain

PY="$FL/.venv/bin/python"

# Kill only THIS LANE's processes. A bare `pkill -f flows_worker` matches every lane on the host,
# so bringing one lane up used to kill another lane's worker out from under a running sweep.
_lane_kill() {
  local pid src
  for pid in $(pgrep -f "$1" 2>/dev/null); do
    [ -r "/proc/$pid/environ" ] || continue   # it exited between pgrep and here
    src=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's/^PYTHONPATH=//p' | head -1)
    case "$src" in "$FL"/src*) kill "$pid" 2>/dev/null ;; esac
  done
}
_lane_kill 'flows_integrations.flows_api'
_lane_kill 'flows_worker' 
sleep 1

cd "$FL"
nohup "$PY" -m uvicorn flows_integrations.flows_api:app --host 127.0.0.1 --port 18200 \
  > "$LOG/flows-api.log" 2>&1 &
echo "flows-api pid=$!"

nohup "$PY" -m flows_worker > "$LOG/flows-worker.log" 2>&1 &
echo "flows-worker pid=$!"

# THE PROVENANCE LINE. "Is the running engine the code I just merged?" was answered for a whole
# revolution by reading a checkout's HEAD and a process's PYTHONPATH — neither of which says WHEN
# the process loaded anything, and both of which look identical on a stale process. Print it at
# startup so the next person reads a log instead of /proc.
echo "flows-engine loaded @ $(git -C "$FL" rev-parse --short HEAD 2>/dev/null || echo unknown)" \
     "($(git -C "$FL" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?'))" \
     "from $FL at $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG/flows-worker.log"

sleep 5
echo "--- flows-api:"
curl -s -m 5 -o /dev/null -w "  GET /flows -> %{http_code}\n" localhost:18200/flows
echo "--- worker log:"
tail -6 "$LOG/flows-worker.log"
echo "--- api log:"
tail -6 "$LOG/flows-api.log"
