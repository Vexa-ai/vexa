#!/usr/bin/env bash
# Replay the whole corpus through BOTH namers, plus the settled-window control.
#
#   CORPUS=<dir of normalized tapes> OUT=<dir> \
#   BASELINE_NAMER=<path> FIX_NAMER=<path> ./run.sh
#
# The two namers are the SAME file at two commits — `git show <commit>:core/meetings/modules/
# mixed-pipeline/src/track-namer.ts`. Nothing else differs between the arms, which is the point:
# a difference in the scorecard can only have come from the namer.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
: "${CORPUS:?}" "${OUT:?}" "${BASELINE_NAMER:?}" "${FIX_NAMER:?}"
TSX="${TSX:-npx tsx}"
mkdir -p "$OUT/baseline" "$OUT/fix" "$OUT/control"

for d in "$CORPUS"/*/; do
  id="$(basename "$d")"
  [ -f "$d/meta.json" ] || continue
  echo "--- $id"
  TAPE="$d" NAMER="$BASELINE_NAMER" $TSX "$HERE/replay.ts" > "$OUT/baseline/$id.json"
  TAPE="$d" NAMER="$FIX_NAMER"      $TSX "$HERE/replay.ts" > "$OUT/fix/$id.json"
  # Settled-window control: replay the fix namer with the roster's discovery window cut away, so
  # the premature-acceptance hazard has no window to fire in. Independent of both arms' verdicts.
  cut="$(python3 - "$d" <<'PY'
import json, sys, os
d = sys.argv[1]
roster = [json.loads(l) for l in open(os.path.join(d, 'roster.jsonl'))] if os.path.exists(os.path.join(d, 'roster.jsonl')) else []
seen, last = set(), 0
for r in roster:
    if r['k'] == 'roster-name' and r.get('name') and r['name'] not in seen:
        seen.add(r['name']); last = max(last, r['t'])
print(last if len(seen) > 1 else 0)
PY
)"
  if [ "$cut" != "0" ]; then
    TAPE="$d" NAMER="$FIX_NAMER" CUT_MS="$cut" $TSX "$HERE/replay.ts" > "$OUT/control/$id.json" || true
  fi
done
echo "done -> $OUT"
