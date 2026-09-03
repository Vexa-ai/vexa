#!/usr/bin/env bash
# blank-instance — return the dogfood stack to a BLANK Vexa: no admin, no users, no desks, no
# meetings, no chats, nothing queued, an empty inbox. The state a customer's box is in the moment
# before its administrator signs in for the first time.
#
# Founder, 2026-09-02: "clean state required for the blank Vexa here to setup global."
#
# THIS IS NOT `reset-instance`. That one releases the admin role and the wizard flags and deletes
# NOTHING, for rehearsing a claim on a stack you want to keep. This one DELETES: every user, every
# desk, every meeting, every chat. Reach for it only when the thing being rehearsed is what a blank
# instance does, and never on a stack whose contents anyone still wants.
#
#   deploy/dogfood/bin/blank-instance.sh --yes [--keep-mail]
#
# WHAT IT DELETES, exactly:
#   1. every row of `platform_settings`            -> no admin wizard state, no company-layer gate
#   2. every `users` row and its api_tokens        -> no admin; the next sign-in claims the instance
#   3. every meeting, session and transcription    -> SQL: no route owns bulk deletion
#   4. every desk in the workspace volume          -> everything except `_global`
#   5. `_global` reduced to `asks/` + `mail/`      -> the preset library and the mail templates
#                                                     SURVIVE; a company layer written into it does not
#   6. redis, BY PREFIX ONLY (see below)           -> no chat threads, no pending scaffolds
#   7. both flows lanes: reactions, receipts,      -> nothing queued, nothing half-run, and no dedup
#      signals, mail cursors and thread memory        memory that would swallow a replayed fact
#   8. mailpit                                     -> an empty inbox (skip with --keep-mail)
#
# WHAT IT NEVER TOUCHES: images, API keys, the SOPS secrets, `~/dna-fixtures`, run directories,
# `_global/.git` history, and any container. It deletes data, never infrastructure — so a wipe always
# leaves a working stack, just an empty one. In redis it never issues FLUSHALL or FLUSHDB: the same
# instance carries the live transcript streams and the bot/meeting machinery, and a blank is about a
# PERSON'S data, not about the deployment's plumbing (see step 6).
#
# It REFUSES while a meeting is live. A wipe mid-meeting destroys the only copy of something nobody
# can re-record, and "no meeting is live" is cheap to check and impossible to remember.
set -euo pipefail

STACK="${VEXA_DOGFOOD_STACK:-vexa-dogfood}"
YES=0
KEEP_MAIL=0
MAILPIT="${VEXA_MAILPIT_URL:-http://127.0.0.1:8025}"

while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --keep-mail) KEEP_MAIL=1; shift ;;
    -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

PG="${STACK}-postgres-1"
AGENT="${STACK}-agent-api-1"
psql_vexa() { docker exec "$PG" psql -U postgres -d vexa -tAc "$1"; }

say()   { printf '  %s\n' "$*"; }
head2() { printf '\n== %s\n' "$*"; }

# ── the refusal, before anything is counted ─────────────────────────────────────────────────────
LIVE=$(psql_vexa "SELECT count(*) FROM meetings WHERE status IN ('active','joining','requested','awaiting_admission','needs_help','stopping');" | tr -d ' ')
if [ "${LIVE:-0}" != "0" ]; then
  echo "REFUSING: $LIVE meeting(s) are live. A wipe mid-meeting destroys the only copy of something" >&2
  echo "nobody can re-record. Wait for them to finish." >&2
  exit 1
fi

# ── say what will go, then require the word ─────────────────────────────────────────────────────
USERS=$(psql_vexa "SELECT count(*) FROM users;" | tr -d ' ')
MEETINGS=$(psql_vexa "SELECT count(*) FROM meetings;" | tr -d ' ')
TOKENS=$(psql_vexa "SELECT count(*) FROM api_tokens;" | tr -d ' ')
SETTINGS=$(psql_vexa "SELECT count(*) FROM platform_settings;" | tr -d ' ')
DESKS=$(docker exec "$AGENT" sh -lc "ls -A /workspaces | grep -vx '_global' | wc -l" | tr -d ' ')
REDIS_KEYS=$(docker exec "${STACK}-redis-1" sh -lc '
  C=$(command -v valkey-cli || command -v redis-cli) || exit 0
  n=0
  for p in "agent:sessions:*" "agent:session:*" "agent:scaffold:*" "agent:scaffolds:by:*"; do
    n=$((n + $($C --scan --pattern "$p" --count 500 | wc -l)))
  done
  echo $n' 2>/dev/null | tr -d ' \r' || echo "?")
MAILS=$(curl -sS "$MAILPIT/api/v1/messages?limit=1" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("messages_count",0))' 2>/dev/null || echo "?")

echo "blank-instance — stack ${STACK}"
say "users .................. $USERS  (all deleted)"
say "api tokens ............. $TOKENS  (all deleted)"
say "meetings ............... $MEETINGS  (all deleted, with sessions + transcriptions)"
say "platform settings ...... $SETTINGS  (all deleted — no admin wizard state, no gate)"
say "desks in the volume .... $DESKS  (all deleted; _global survives)"
say "mailpit messages ....... $MAILS  $([ "$KEEP_MAIL" = 1 ] && echo '(KEPT: --keep-mail)' || echo '(all deleted)')"
say "_global ................ reduced to asks/ + mail/ (+ .git history, kept)"
say "redis (BY PREFIX) ...... $REDIS_KEYS  chat-thread index + scaffold records; nothing else"
say "flows (both lanes) ..... reactions, receipts, signals, cursors, thread memory"

if [ "$YES" != "1" ]; then
  echo
  echo "Nothing was deleted. Re-run with --yes to do it." >&2
  exit 3
fi

echo
echo "wiping at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── 0 · the per-turn workers. They are not stack services, and a parked one un-blanks the wipe. ──
head2 "agent workers"
# ⚠ A worker container outlives the turn that spawned it (warm-window TTL), holds its subject's desk
# BOUND READ-WRITE, and will happily re-create /workspaces/<uid> on its next write — for a uid this
# script has just deleted. Observed 2026-09-02: `vexa-worker-124-chat-…` was still Up after a blank,
# owned by a user that no longer existed. A wipe that leaves a live writer attached to the thing it
# wiped is not a wipe; it is a race it has not lost yet. These are per-turn containers the runtime
# re-creates on demand, so removing them costs nothing.
W=$(docker ps -aq --filter "name=vexa-worker-" | wc -l | tr -d ' ')
if [ "$W" != "0" ]; then
  docker ps -a --filter "name=vexa-worker-" --format '    {{.Names}}  {{.Status}}'
  docker rm -f $(docker ps -aq --filter "name=vexa-worker-") >/dev/null 2>&1 || true
fi
say "removed $W worker container(s); $(docker ps -aq --filter 'name=vexa-worker-' | wc -l | tr -d ' ') left"

# ── 1-3 · identity + product data. No route owns bulk deletion, so this is SQL, in FK order. ────
head2 "postgres (vexa)"
psql_vexa "DELETE FROM transcriptions;"     | sed 's/^/  transcriptions:     /'
psql_vexa "DELETE FROM meeting_sessions;"   | sed 's/^/  meeting_sessions:   /'
psql_vexa "DELETE FROM meetings;"           | sed 's/^/  meetings:           /'
psql_vexa "DELETE FROM api_tokens;"         | sed 's/^/  api_tokens:         /'
psql_vexa "DELETE FROM users;"              | sed 's/^/  users:              /'
psql_vexa "DELETE FROM platform_settings;"  | sed 's/^/  platform_settings:  /'

# ── 4-5 · the workspace volume. `_global` survives, minus any company layer written into it. ────
head2 "workspace volume"
docker exec "$AGENT" sh -lc '
  cd /workspaces || exit 1
  for d in $(ls -A . | grep -vx "_global"); do rm -rf -- "$d"; done
  cd _global || exit 0
  for f in $(ls -A . | grep -vxE "asks|mail|\.git"); do rm -rf -- "$f"; done
  if [ -d .git ]; then
    git add -A
    git -c user.email=platform@vexa.local -c user.name=vexa-platform \
        commit -q -m "blank instance: the company layer is removed; presets and mail templates stay" || true
  fi
  echo "  /workspaces now: $(cd /workspaces && ls -A | tr "\n" " ")"
  echo "  _global now:     $(cd /workspaces/_global && ls -A | tr "\n" " ")"
'

# ── 6 · redis, BY PREFIX. Never FLUSHALL. ───────────────────────────────────────────────────────
head2 "redis"
# ⚠ THE BLANK USED TO STOP AT POSTGRES AND THE VOLUME, AND REDIS IS THE THIRD STORE.
# Found 2026-09-02 while building the scaffold (biz#449): agent-api keeps two durable things here —
# the per-subject CHAT-THREAD INDEX (`_Sessions`) and the SCAFFOLD RECORDS (`ScaffoldStore`) — and a
# blank left every one of them behind. 335 session keys survived the two blanks of that morning. A
# scaffold outliving a blank is worse than untidy: it is a live capability, minted for an address,
# still redeemable on an instance that has been handed to somebody else.
#
# ONLY THESE PREFIXES, and they are listed rather than globbed on `agent:*` on purpose — a prefix
# list is reviewable, `agent:*` silently adopts whatever the next feature names. Everything here is
# ONE PERSON'S data:
#
#   agent:sessions:<subject>            the subject's chat-thread id set        (api.py `_Sessions`)
#   agent:session:<subject>:<session>   one thread's title + timestamps         (api.py `_Sessions`)
#   agent:scaffold:<id>                 one arrival record                      (scaffolds.py)
#   agent:scaffolds:by:<address>        that recipient's pending-scaffold index (scaffolds.py)
#
# AND NOTHING ELSE. `FLUSHALL`/`FLUSHDB` would also take `tc:meeting:*` (the live transcript
# streams the copilot tails and the db-writer drains), the `unit:*` per-dispatch streams, and the
# bot machinery — deployment plumbing, and in the transcript case the only copy of something nobody
# can re-record. The refusal at the top of this script exists for exactly that reason; a flush here
# would walk straight around it.
#
# NOT cleared, deliberately, and said out loud rather than left to be discovered: `unit:*` (the
# per-dispatch chat streams) and `tc:meeting:*` (transcript streams) are orphaned by this wipe and
# accumulate. They are GARBAGE, not a hazard — `dispatch_id` is derived from (subject, session) and
# postgres never reuses a deleted user's id, so a fresh person cannot land on an old stream. Cleaning
# them is a retention question, not a blanking one, and it is filed rather than smuggled in here.
REDIS="${STACK}-redis-1"
REDIS_PREFIXES="agent:sessions:* agent:session:* agent:scaffold:* agent:scaffolds:by:*"
# valkey since #653; `redis-cli` is kept as the fallback so this works against either engine.
RCLI=$(docker exec "$REDIS" sh -lc 'command -v valkey-cli || command -v redis-cli' 2>/dev/null | tr -d '\r')
if [ -z "$RCLI" ]; then
  echo "REFUSING: no valkey-cli/redis-cli in $REDIS — the chat-thread index and any pending" >&2
  echo "scaffold would survive the blank, and a scaffold is a live capability." >&2
  exit 1
fi
for pat in $REDIS_PREFIXES; do
  # SCAN, never KEYS: KEYS blocks the server for the whole sweep, and this one also serves the live
  # transcript streams. The delete is batched through xargs so a large sweep is not one call per key.
  n=$(docker exec "$REDIS" sh -lc "$RCLI --scan --pattern '$pat' --count 500 | wc -l" | tr -d ' \r')
  if [ "${n:-0}" != "0" ]; then
    docker exec "$REDIS" sh -lc \
      "$RCLI --scan --pattern '$pat' --count 500 | xargs -r -n 200 $RCLI DEL >/dev/null"
  fi
  printf '    %-26s %s\n' "$pat" "$n deleted"
done
# … and PROVE it, for the same reason the flows lane below proves itself: a step that cannot do its
# job and says so quietly is indistinguishable from one that worked.
for pat in $REDIS_PREFIXES; do
  left=$(docker exec "$REDIS" sh -lc "$RCLI --scan --pattern '$pat' --count 500 | wc -l" | tr -d ' \r')
  [ "${left:-0}" = "0" ] || { echo "REFUSING to report success: redis still holds $left key(s) matching $pat" >&2; exit 1; }
done
say "verified: no chat-thread index and no scaffold records remain"

# ── 7 · every flows lane. Through the POSTGRES CONTAINER, in dependency order, loudly. ─────
head2 "flows"
# ⚠ WHY THIS RUNS `docker exec` AND NOT `psql`. The first version of this script called `psql` on
# the HOST, where there is no psql binary. Every delete failed, every failure was caught and printed
# as the word "skip", and the wipe reported success while leaving 115 reactions in `flows` and 123
# in `flows_sim` — parked reactions referencing users that no longer existed, which would have fired
# the moment the gate lifted. Same class as every other defect this night: a step that cannot do its
# job and says so in a word nobody reads is indistinguishable from one that worked. It now FAILS
# LOUDLY and refuses to report success it has not verified.
#
# Lanes are DISCOVERED, not listed. Reading ~/.storm/dburl finds the lane somebody remembered; a
# `flows%` scan of the stack own postgres finds every lane that exists, including the ones nobody
# did. The sim lane was exactly that case.
FLOW_DBS=$(docker exec "$PG" psql -U postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname LIKE 'flows%' ORDER BY 1;" | tr -d ' \r')
[ -n "$FLOW_DBS" ] || { echo "REFUSING: no flows lane database found in $PG" >&2; exit 1; }

# DEPENDENCY ORDER, and it is not alphabetical: effect_receipt and signal reference reaction, so
# reaction is deleted LAST or the foreign key refuses it.
FLOW_TABLES="effect_receipt signal reaction mail_cursor mail_seen mail_thread mail_outbox_sent friction"
for db in $FLOW_DBS; do
  say "lane $db"
  for t in $FLOW_TABLES; do
    if ! out=$(docker exec "$PG" psql -U postgres -d "$db" -tAc "DELETE FROM $t;" 2>&1); then
      # A table this lane does not have is not a failure — lanes differ (`friction` exists in one
      # and not the other) — but it must be SAID, not swallowed. Anything else is fatal: a lane
      # left with rows fires parked work at users that no longer exist.
      case "$out" in
        *"does not exist"*) printf '    %-18s %s\n' "$t" "absent in this lane — skipped"; continue ;;
        *) echo "  FAILED on $db.$t: $out" >&2
           echo "  A flows lane left with rows fires parked work at users that no longer exist." >&2
           exit 1 ;;
      esac
    fi
    printf '    %-18s %s\n' "$t" "$out"
  done
done

# … and PROVE it. The whole defect above was that the failure was invisible, so the fix is not
# only to fail loudly but to refuse to CLAIM success without reading the counts back.
for db in $FLOW_DBS; do
  left=$(docker exec "$PG" psql -U postgres -d "$db" -tAc \
    "SELECT (SELECT count(*) FROM reaction)+(SELECT count(*) FROM signal)+(SELECT count(*) FROM effect_receipt);" | tr -d ' \r')
  [ "$left" = "0" ] || { echo "REFUSING to report success: $db still holds $left row(s)" >&2; exit 1; }
  say "lane $db verified empty"
done

# ── 8 · the inbox ───────────────────────────────────────────────────────────────────────────────
head2 "mailpit"
if [ "$KEEP_MAIL" = 1 ]; then
  say "kept (--keep-mail)"
else
  curl -sS -X DELETE "$MAILPIT/api/v1/messages" >/dev/null && say "emptied"
fi

# ── what a blank instance looks like ────────────────────────────────────────────────────────────
head2 "state now"
say "users:             $(psql_vexa 'SELECT count(*) FROM users;' | tr -d ' ')"
say "platform settings: $(psql_vexa 'SELECT count(*) FROM platform_settings;' | tr -d ' ')"
say "meetings:          $(psql_vexa 'SELECT count(*) FROM meetings;' | tr -d ' ')"
say "mailpit:           $(curl -sS "$MAILPIT/api/v1/messages?limit=1" 2>/dev/null | python3 -c 'import json,sys;print(json.load(sys.stdin).get("messages_count",0))' 2>/dev/null || echo '?')"
echo
cat <<'NEXT'
  A blank Vexa. The next sign-in claims the instance, meets the wizard, and writes the company
  layer; until it does, no other person can sign in, the flows engine parks every fact instead of
  sending, and the operator verbs refuse.

  Kept on purpose: _global/asks/ (the preset library), _global/mail/ (the mail templates) and
  _global/.git (the history). Those are the deployment's own furniture, not anybody's data. In redis
  the same rule, from the other side: the chat-thread index and every scaffold are gone, and the
  transcript streams and per-dispatch streams are untouched — plumbing, never a person.
NEXT
