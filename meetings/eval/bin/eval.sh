#!/usr/bin/env bash
# eval — entrypoint for the synthetic-meeting harness. Sources secrets.env, then
# runs a stage. Usage:
#   ./bin/eval.sh launch     # send the speaker bots into the meeting (staggered)
#   ./bin/eval.sh drive      # make admitted bots speak the timeline + log truth
#   ./bin/eval.sh judge      # score the live transcript vs ground truth
#   ./bin/eval.sh corpus     # (re)generate the TTS clip pools (FORCE_REGEN=1)
#   ./bin/eval.sh observe    # LIVE-watch a session's transcript dynamics (local, no secrets)
# All knobs are env (see README / src/drive.mjs). e.g. GAP_MEAN=-0.5 ./bin/eval.sh drive
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# `observe` is a LOCAL live-watch (taps the desktop /ws on localhost) — no secrets, no deps.
[ "${1:-}" = "observe" ] && exec node "$HERE/src/observe.mjs" "${@:2}"
SECRETS="${SECRETS:-$HERE/secrets.env}"
[ -f "$SECRETS" ] && { set -a; . "$SECRETS"; set +a; } || { echo "no secrets.env ($SECRETS) — cp secrets.env.example secrets.env"; exit 1; }
case "${1:-}" in
  launch) exec node "$HERE/src/launch.mjs" ;;
  drive)  exec node "$HERE/src/drive.mjs" ;;
  corpus) exec node "$HERE/src/corpus.mjs" ;;
  judge)  exec python3 "$HERE/src/judge.py" ;;
  *) echo "usage: eval.sh {launch|drive|judge|corpus|observe}"; exit 2 ;;
esac
