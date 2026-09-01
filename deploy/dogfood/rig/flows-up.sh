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

pkill -f 'flows_integrations.flows_api' 2>/dev/null
pkill -f 'flows_worker' 2>/dev/null
sleep 1

cd "$FL"
nohup "$PY" -m uvicorn flows_integrations.flows_api:app --host 127.0.0.1 --port 18200 \
  > "$LOG/flows-api.log" 2>&1 &
echo "flows-api pid=$!"

nohup "$PY" -m flows_worker > "$LOG/flows-worker.log" 2>&1 &
echo "flows-worker pid=$!"

sleep 5
echo "--- flows-api:"
curl -s -m 5 -o /dev/null -w "  GET /flows -> %{http_code}\n" localhost:18200/flows
echo "--- worker log:"
tail -6 "$LOG/flows-worker.log"
echo "--- api log:"
tail -6 "$LOG/flows-api.log"
