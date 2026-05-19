#!/usr/bin/env bash
# v0.10.6.1-bot-acceptance-signals — pins the bot-acceptance-signal
# collection + persistence pipeline. The bot collects signals (e.g.
# first_audio_received) into window.__vexaAcceptanceSignals; the
# acceptance-signals service reads them; meeting-api callbacks persists
# them to meeting.data["bot_acceptance_signals"] with history. This is
# the data that backs the human bot-acceptance-bar UAT call.
#
# Steps:
#   bot_collects_signals      Static: services/vexa-bot/core/src/
#                             services/acceptance-signals.ts reads
#                             window.__vexaAcceptanceSignals, and
#                             services/vexa-bot/core/src/index.ts
#                             writes first_audio_received on the first
#                             Teams audio chunk (the canary signal).
#   callbacks_persist_signals Static: services/meeting-api/meeting_api/
#                             callbacks.py accepts acceptance_signals
#                             on the inbound payload, writes them to
#                             meeting.data["bot_acceptance_signals"]
#                             and appends to a history array.

source "$(dirname "$0")/../lib/common.sh"

ROOT_DIR="${ROOT:-$(git rev-parse --show-toplevel)}"
COLLECTOR="$ROOT_DIR/services/vexa-bot/core/src/services/acceptance-signals.ts"
BOT_INDEX="$ROOT_DIR/services/vexa-bot/core/src/index.ts"
CALLBACKS="$ROOT_DIR/services/meeting-api/meeting_api/callbacks.py"

step="${1:?usage: $0 <step>}"

echo ""
echo "  v0.10.6.1-bot-acceptance-signals :: $step"
echo "  ──────────────────────────────────────────────"
test_begin "v0.10.6.1-bot-acceptance-signals-$step"

case "$step" in
  bot_collects_signals)
    failed=0

    if [ ! -f "$COLLECTOR" ]; then
      echo "    FAIL: acceptance-signals.ts missing"
      step_fail "COLLECTOR_PRESENT" "file not found"
      failed=1
    else
      step_pass "COLLECTOR_PRESENT" "acceptance-signals.ts present"
      if ! grep -q '__vexaAcceptanceSignals' "$COLLECTOR"; then
        echo "    FAIL: collector does not read window.__vexaAcceptanceSignals"
        step_fail "COLLECTOR_READS_WINDOW" "window symbol not referenced"
        failed=1
      else
        step_pass "COLLECTOR_READS_WINDOW" "collector reads window.__vexaAcceptanceSignals"
      fi
      if ! grep -qE 'export async function collectAcceptanceSignals' "$COLLECTOR"; then
        echo "    FAIL: collector does not export collectAcceptanceSignals"
        step_fail "COLLECTOR_EXPORT" "export missing"
        failed=1
      else
        step_pass "COLLECTOR_EXPORT" "collectAcceptanceSignals exported"
      fi
    fi

    if [ ! -f "$BOT_INDEX" ]; then
      echo "    FAIL: vexa-bot index.ts missing"
      step_fail "INDEX_PRESENT" "file not found"
      failed=1
    else
      if ! grep -q 'first_audio_received' "$BOT_INDEX"; then
        echo "    FAIL: index.ts does not emit first_audio_received"
        step_fail "FIRST_AUDIO_EMITTED" "canary signal not set"
        failed=1
      else
        step_pass "FIRST_AUDIO_EMITTED" "index.ts emits first_audio_received canary"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  callbacks_persist_signals)
    failed=0

    if [ ! -f "$CALLBACKS" ]; then
      echo "    FAIL: callbacks.py missing"
      step_fail "CALLBACKS_PRESENT" "file not found"
      failed=1
    else
      step_pass "CALLBACKS_PRESENT" "callbacks.py present"

      # Payload field accepted.
      if ! grep -qE 'acceptance_signals:\s*Optional\[Dict' "$CALLBACKS"; then
        echo "    FAIL: callbacks payload model does not declare acceptance_signals"
        step_fail "PAYLOAD_FIELD" "Optional[Dict] not declared"
        failed=1
      else
        step_pass "PAYLOAD_FIELD" "callbacks payload accepts acceptance_signals dict"
      fi

      # Persisted to meeting.data under bot_acceptance_signals.
      if ! grep -qE 'bot_acceptance_signals.*payload\.acceptance_signals' "$CALLBACKS"; then
        echo "    FAIL: callbacks does not write meeting.data['bot_acceptance_signals']"
        step_fail "MEETING_DATA_WRITE" "destination key not written"
        failed=1
      else
        step_pass "MEETING_DATA_WRITE" "callbacks writes meeting.data['bot_acceptance_signals']"
      fi

      # History append (not just overwrite).
      if ! grep -qE 'history\.append\(payload\.acceptance_signals\)' "$CALLBACKS"; then
        echo "    FAIL: callbacks does not append to a history list"
        step_fail "HISTORY_APPEND" "history append missing"
        failed=1
      else
        step_pass "HISTORY_APPEND" "callbacks appends each signal observation to history"
      fi
    fi

    if [ "$failed" -eq 1 ]; then
      test_end; exit 1
    fi
    ;;

  *)
    echo "    FAIL: unknown step '$step'"
    test_end
    exit 1
    ;;
esac

test_end
echo ""
echo "  PASS"
