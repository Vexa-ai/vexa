#!/usr/bin/env bash
# The SIM lane's inbound poller, on the LINE source. Reads mailpit for mail to vexa@sim.test and
# admits invite.received / mail.reply into flows_sim. Twin of ~/.storm/sim-mailbox-up.sh; the only
# change is the source tree (wt-adoption-sim -> wt-line).
set -u
FL_SRC="${VEXA_FLOWS_SRC:-$(cd "$(dirname "$0")/../../../core/flows" && pwd)}/src"
VENV="/home/dima/dev/vexa-flows1315/core/flows/.venv/bin/python"
export PYTHONPATH="$FL_SRC"
BASE=$(sed 's#/flows$##' "$HOME/.storm/dburl")
export VEXA_FLOWS_DB_URL="$BASE/flows_sim"
export VEXA_MAIL_ADDR="vexa@sim.test"
export VEXA_MAIL_INBOX=mailpit
export VEXA_MAILPIT_URL=http://127.0.0.1:8025
export VEXA_MAIL_SMTP_HOST=127.0.0.1
export VEXA_MAIL_SMTP_PORT=1025
export VEXA_MAIL_SMTP_MODE=plain
# Flows reaches the other services only through the gateway's service door (port 8002 of the
# gateway container, published on no host port), with the flows service key the gateway holds.
# Flows holds no admin key and no internal secret.
GW_CONTAINER="${VEXA_DOGFOOD_GATEWAY:-vexa-dogfood-gateway-1}"
DOOR="${VEXA_FLOWS_DOOR_URL:-http://$(docker inspect "$GW_CONTAINER" \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' 2>/dev/null | awk '{print $1}'):8002}"
export VEXA_FLOWS_GATEWAY_URL="$DOOR/meetings"
export VEXA_FLOWS_AGENT_API_URL="$DOOR/agent"
export VEXA_FLOWS_ADMIN_API_URL="$DOOR/identity"
export VEXA_FLOWS_SERVICE_KEY="$(docker inspect "$GW_CONTAINER" \
  --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null | grep '^VEXA_FLOWS_SERVICE_KEY=' | cut -d= -f2-)"
if [ -z "${VEXA_FLOWS_SERVICE_KEY:-}" ] || [ "$DOOR" = "http://:8002" ]; then
  echo "sim-mailbox: REFUSING to start — no flows service key on $GW_CONTAINER, or no address for its door." >&2
  exit 1
fi
export VEXA_UI_URL="https://app.dev.vexa.ai"
export VEXA_FLOWS_ATTENDEE_DOMAINS="rehearse.test,rehearsal.test,studio.example,bank.example"
mkdir -p /tmp/sim-logs
cd "$FL_SRC" || exit 1
exec "$VENV" -u -m flows_integrations.mailbox 2>&1 | tee -a /tmp/sim-logs/mailbox.log
