#!/usr/bin/env bash
# Bring up the flows half of the storm hot loop on bbb.
# Processes (not containers) so edits in the worktree are live on restart.
set -u
# Same VEXA_FLOWS_SRC the rig and the control server read — one name for the flows checkout,
# defaulted to what this host has always used so an unconfigured run is unchanged.
# ONE LINE (2026-09-02): the engine runs from the line worktree, not the old lineage.
# Previous value, kept for rollback:
#   FL="${VEXA_FLOWS_SRC:-/home/dima/dev/vexa-flows1315/core/flows}"
FL="${VEXA_FLOWS_SRC:-/home/dima/dev/wt-line/core/flows}"
# The interpreter stays where the venv is — the line worktree has no .venv of its own and
# the dependencies are identical. Source and interpreter are different questions.
VENV="${VEXA_FLOWS_PY:-/home/dima/dev/vexa-flows1315/core/flows/.venv/bin/python}"
LOG=/tmp/storm-logs
mkdir -p "$LOG"

export PYTHONPATH="$FL/src"
export VEXA_FLOWS_DB_URL="$(cat "$HOME/.storm/dburl")"
export VEXA_FLOWS_GATEWAY_URL="http://localhost:18456"
export VEXA_FLOWS_AGENT_API_URL="http://localhost:18500"
export VEXA_FLOWS_ADMIN_API_URL="http://localhost:18457"
# The flows-api OPERATOR key. Its own secret, in its own mode-600 file — NOT the admin-api
# token, which is what VEXA_FLOWS_ADMIN_KEY holds and what this was silently confused with:
# flows-api reads VEXA_FLOWS_API_KEY, that name was never exported, and the module defaulted
# to the string "changeme". flows-api now refuses to start without this.
export VEXA_FLOWS_API_KEY="$(cat "$HOME/.storm/flows-api-key")"
export VEXA_FLOWS_ADMIN_KEY="$(docker inspect vexa-dogfood-admin-api-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ADMIN_API_TOKEN=' | cut -d= -f2)"

# Where a person's own terminal lives — the host every link a flow sends must name. Same env
# name the control MCP reads; a mail that says "open it here" and names a host they cannot
# reach is worse than a mail with no link.
export VEXA_UI_URL="https://app.dev.vexa.ai"

# the mail double — nothing can reach a real mailbox from this loop
export VEXA_MAIL_ADDR="vexa@storm.test"
export VEXA_MAIL_SMTP_HOST=127.0.0.1
export VEXA_MAIL_SMTP_PORT=1025
export VEXA_MAIL_SMTP_MODE=plain

PY="$VENV"

# LANE-SCOPED, and it has to be. These patterns used to be bare — 'flows_integrations.flows_api'
# and 'flows_worker' — which match EVERY lane on the box, not this one. A second lane now exists
# (the adoption simulator on :18201 with its own database), and restarting this lane silently
# killed that one: its api runs the same module, and 'flows_worker' is a substring of
# 'sim_flows_worker'. The other lane did not error, it just stopped, and the next call against it
# failed with a connection error that pointed nowhere near this file.
# The port makes the api unambiguous; '-m flows_worker' matches this worker's argv and not a
# lane that starts its worker any other way.
pkill -f "flows_integrations.flows_api:app --host 127.0.0.1 --port 18200" 2>/dev/null
pkill -f -- "-m flows_worker" 2>/dev/null
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
