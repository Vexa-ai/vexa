#!/usr/bin/env bash
# Offline re-scoring of an existing dataset.
# No live meeting needed — reads captured data from testdata/.
#
# Usage:
#   make -C tests3 score DATASET=gmeet-compose-260405
#   make -C tests3 score DATASET=teams-compose-260405
#
# Reads: testdata/{DATASET}/ground-truth.json + testdata/{DATASET}/pipeline/rest-segments.json
# Writes: testdata/{DATASET}/pipeline/score.json (overwritten)
source "$(dirname "$0")/../lib/common.sh"

DATASET=${DATASET:-}
T3=$(cd "$(dirname "$0")/.." && pwd)

echo ""
echo "  score"
echo "  ══════════════════════════════════════════════"

if [ -z "$DATASET" ]; then
    echo ""
    echo "  Available datasets:"
    for d in "$T3"/testdata/*/ground-truth.json; do
        [ -f "$d" ] || continue
        name=$(basename "$(dirname "$d")")
        segs="$T3/testdata/$name/pipeline/rest-segments.json"
        if [ -f "$segs" ]; then
            echo "    $name"
        else
            echo "    $name  (no pipeline/rest-segments.json — needs collection)"
        fi
    done
    echo ""
    echo "  Usage: make -C tests3 score DATASET=<name>"
    exit 1
fi

GT="$T3/testdata/$DATASET/ground-truth.json"
SEGS="$T3/testdata/$DATASET/pipeline/rest-segments.json"
OUT="$T3/testdata/$DATASET/pipeline/score.json"

if [ ! -f "$GT" ]; then
    fail "ground truth not found: $GT"
    exit 1
fi

if [ ! -f "$SEGS" ]; then
    fail "segments not found: $SEGS"
    info "run 'make collect' first to capture pipeline output"
    exit 1
fi

info "dataset: $DATASET"
info "ground truth: $(python3 -c "import json; print(len(json.load(open('$GT'))))" 2>/dev/null) utterances"
info "segments: $(python3 -c "
import json
d=json.load(open('$SEGS'))
segs=d.get('segments',[]) if isinstance(d,dict) else d
print(len(segs))
" 2>/dev/null) segments"

# Run scorer (stdout = JSON, stderr = human summary)
python3 "$T3/lib/score.py" --gt "$GT" --segments "$SEGS" > "$OUT"

# Extract metrics from score.json
RESULT=$(python3 -c "
import json
s=json.load(open('$OUT'))
print(f\"pass={s['pass']}/{s['gt_count']} missed={s['missed']} hallucinations={s['hallucinations']}\")
print(f\"speaker_accuracy={s['speaker_accuracy']:.0%} similarity={s['avg_similarity']:.1%} completeness={s['completeness']:.0%}\")
" 2>/dev/null)

echo "$RESULT" | while IFS= read -r line; do
    pass "$line"
done

info "score written: $OUT"

echo "  ══════════════════════════════════════════════"
echo ""
