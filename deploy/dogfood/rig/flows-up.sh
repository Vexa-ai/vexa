#!/usr/bin/env bash
# Bring up the flows half of the storm hot loop on bbb.
# Processes (not containers) so edits in the worktree are live on restart.
set -u
FL=/home/dima/dev/vexa-flows1315/core/flows
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

# ...and its INBOUND half. Mailpit speaks no IMAP and no POP3, so the mailbox poller reads its
# REST API instead: VEXA_MAIL_INBOX selects the source, VEXA_MAIL_ADDR (above) is the recipient
# it answers as, and no mail password is needed on this path at all.
#   imap (default) -> imap.gmail.com, unchanged   |   mailpit -> the double
export VEXA_MAIL_INBOX=mailpit
export VEXA_MAILPIT_URL=http://127.0.0.1:8025
export VEXA_MAILPIT_LOOKBACK_S=300         # re-scan window behind the Created watermark

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

# The inbound poller is NOT started here: it ADMITS FACTS, so it goes up deliberately, once the
# rehearsal actually wants the box read. Start it by hand with the env this script exported:
#   cd "$FL/src" && nohup "$PY" -m flows_integrations.mailbox > "$LOG/flows-mailbox.log" 2>&1 &
# First boot anchors at the CURRENT tail, so the double's rehearsal history is never replayed;
# pass an ISO timestamp as argv[1] to rewind deliberately.

sleep 5
echo "--- flows-api:"
curl -s -m 5 -o /dev/null -w "  GET /flows -> %{http_code}\n" localhost:18200/flows
echo "--- worker log:"
tail -6 "$LOG/flows-worker.log"
echo "--- api log:"
tail -6 "$LOG/flows-api.log"
