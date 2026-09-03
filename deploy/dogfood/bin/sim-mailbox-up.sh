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
export VEXA_FLOWS_GATEWAY_URL="http://localhost:18456"
export VEXA_FLOWS_AGENT_API_URL="http://localhost:18500"
export VEXA_FLOWS_ADMIN_API_URL="http://localhost:18457"
export VEXA_FLOWS_ADMIN_KEY="$(docker inspect vexa-dogfood-admin-api-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^ADMIN_API_TOKEN=' | cut -d= -f2)"
export VEXA_INTERNAL_SECRET="$(cat "$HOME/.storm/internal-secret")"
export VEXA_UI_URL="https://app.dev.vexa.ai"
export VEXA_FLOWS_ATTENDEE_DOMAINS="rehearse.test,rehearsal.test,imageworks.example,bank.example"
mkdir -p /tmp/sim-logs
cd "$FL_SRC" || exit 1
exec "$VENV" -u -m flows_integrations.mailbox 2>&1 | tee -a /tmp/sim-logs/mailbox.log
