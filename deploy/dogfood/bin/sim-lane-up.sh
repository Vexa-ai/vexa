#!/usr/bin/env bash
# Bring the SIM lane (db flows_sim, api :18201, mailbox vexa@sim.test) up on the LINE source.
# Twin of ~/.storm/flows-up.sh; the only differences are the four lane-scoped values marked below.
# Restore the old lane with:  bash ~/.storm/sim-flows-up.sh
set -u
FL="${VEXA_FLOWS_SRC:-$(cd "$(dirname "$0")/../../../core/flows" && pwd)}"          # <- the line, not wt-adoption-sim
VENV="/home/dima/dev/vexa-flows1315/core/flows/.venv/bin/python"    # the venv, as both lanes use
LOG=/tmp/sim-logs
mkdir -p "$LOG"

export PYTHONPATH="$FL/src"
BASE=$(sed 's#/flows$##' "$HOME/.storm/dburl")
export VEXA_FLOWS_DB_URL="$BASE/flows_sim"                          # <- lane db
export VEXA_FLOWS_GATEWAY_URL="http://localhost:18456"
export VEXA_FLOWS_AGENT_API_URL="http://localhost:18500"
export VEXA_FLOWS_ADMIN_API_URL="http://localhost:18457"
export VEXA_FLOWS_API_KEY="$(cat "$HOME/.storm/sim-flows-api-key")" # <- lane operator key
export VEXA_FLOWS_ADMIN_KEY="$(docker inspect vexa-dogfood-admin-api-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ADMIN_API_TOKEN=' | cut -d= -f2)"
export VEXA_INTERNAL_SECRET="$(cat "$HOME/.storm/internal-secret" 2>/dev/null)"
if [ -z "${VEXA_INTERNAL_SECRET:-}" ]; then
  echo 'sim-lane: REFUSING to start — no internal-tier secret; every meeting room would silently read zero desks.' >&2
  exit 1
fi
export VEXA_UI_URL="https://app.dev.vexa.ai"
export VEXA_MAIL_ADDR="vexa@sim.test"                                # <- lane mailbox
export VEXA_MAIL_SMTP_HOST=127.0.0.1
export VEXA_MAIL_SMTP_PORT=1025
export VEXA_MAIL_SMTP_MODE=plain
export VEXA_MAIL_INBOX=mailpit
export VEXA_MAILPIT_URL=http://127.0.0.1:8025
export VEXA_MAILPIT_LOOKBACK_S=300
# The attendee fan-out's allow-list. The sim org's own domains, PLUS the rehearsal domain — without
# it every follow-up to an @rehearse.test attendee is filtered and the flow reports success having
# mailed nobody (the F3 defect, exactly).
export VEXA_FLOWS_ATTENDEE_DOMAINS="rehearse.test,rehearsal.test,imageworks.example,bank.example"

# LANE-SCOPED KILLS ONLY. '-m flows_worker' is the FOUNDER lane's argv and must never appear here;
# the sim worker keeps its renamed argv0 for the same reason, so the founder lane's own pkill
# cannot reach it. (flows-up.sh learned this the hard way and wrote it down.)
pkill -f 'flows_integrations.flows_api:app --host 127.0.0.1 --port 18201' 2>/dev/null
pkill -f 'sim_flows_worker' 2>/dev/null
sleep 1

cd "$FL" || exit 1
nohup "$VENV" -m uvicorn flows_integrations.flows_api:app --host 127.0.0.1 --port 18201 \
  > "$LOG/api.log" 2>&1 &
echo "sim flows-api pid=$!"
nohup "$VENV" -c "import sys; sys.argv[0]='sim_flows_worker'; import runpy; runpy.run_module('flows_worker', run_name='__main__')" \
  > "$LOG/worker.log" 2>&1 &
echo "sim flows-worker pid=$!"

sleep 6
echo '--- flows-api:'; curl -s -m 5 -o /dev/null -w '  GET :18201/flows -> %{http_code}\n' localhost:18201/flows
echo '--- worker log:'; tail -8 "$LOG/worker.log"
echo '--- api log:';    tail -4 "$LOG/api.log"
