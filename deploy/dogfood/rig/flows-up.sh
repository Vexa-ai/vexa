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

# The INTERNAL-TIER secret, for the one call that opens a meeting ROOM.
#
# The post-meeting run reads the desks of the people who were in the meeting, and agent-api will
# only open that room for an INTERNAL caller: `POST /api/chat` checks `X-Internal-Secret` against
# its own `VEXA_INTERNAL_API_SECRET` and, absent a match, mounts nothing extra and says so. That
# fail-closed shape is deliberate — a browser client coming through the gateway can never open a
# room — but it also means flows without this secret is INERT: every run would read zero desks and
# nothing would look broken.
#
# Its own mode-600 file, like the operator key above and for the same reason: the value belongs to
# the deployment, never to the repo, never to a log, never to an error message. The NAME is
# documented here; the VALUE lives only in $HOME/.storm/internal-secret.
# It must equal the agent-api container's VEXA_INTERNAL_API_SECRET — if agent-api is ever recreated
# with a different one, the room silently stops opening, so re-copy it then.
export VEXA_INTERNAL_SECRET="$(cat "$HOME/.storm/internal-secret" 2>/dev/null)"
if [ -z "${VEXA_INTERNAL_SECRET:-}" ]; then
  echo "flows-up: REFUSING to start — no internal-tier secret at $HOME/.storm/internal-secret." >&2
  echo "  Without it agent-api refuses every meeting room and the post-meeting run reads no desks," >&2
  echo "  silently. Copy agent-api's VEXA_INTERNAL_API_SECRET into that file (chmod 600)." >&2
  exit 1
fi

# Where a person's own terminal lives — the host every link a flow sends must name. Same env
# name the control MCP reads; a mail that says "open it here" and names a host they cannot
# reach is worse than a mail with no link.
export VEXA_UI_URL="https://app.dev.vexa.ai"

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
