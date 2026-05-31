#!/bin/bash
# Run the offline diarizer harness in a vexa-bot:dev container.
# Mounts host core/src (latest fixed TS) + harness + samples + shared model cache.
#   run_eval.sh <sample.wav-basename> <out.json-basename> <config-json> [label]
set -euo pipefail
REPO="/home/dima/dev/vexa-pack-pack-msteams-diarization-cutover"
CORE="$REPO/services/vexa-bot/core"
SAMPLES="$REPO/.agents/rnd/diarization-eval/samples"
WAV="$1"; OUT="$2"; CFG="$3"; LABEL="${4:-eval}"

docker volume create diar-hfcache >/dev/null

# CPUSET pins the container to a disjoint slice of host cores. This is the
# ONLY reliable way to bound onnxruntime-node — it reads the visible core
# count (cpuset), not OMP_NUM_THREADS, to size its thread pool. Without this,
# every container spawns ~128 threads and N>1 containers thrash the box.
CPUSET="${CPUSET:-}"
CPUSET_ARG=""
[ -n "$CPUSET" ] && CPUSET_ARG="--cpuset-cpus=$CPUSET"
docker run --rm \
  --name "diar-$LABEL" \
  $CPUSET_ARG \
  -v "$CORE/src:/app/vexa-bot/core/src:ro" \
  -v "$CORE/eval-diarizer.ts:/app/vexa-bot/core/eval-diarizer.ts:ro" \
  -v "$SAMPLES:/data" \
  -v "diar-hfcache:/hfcache" \
  -e DIAR_CACHE=/hfcache \
  -w /app/vexa-bot/core \
  --entrypoint bash \
  vexaai/vexa-bot:dev \
  -c "npx tsx eval-diarizer.ts --wav /data/$WAV --out /data/$OUT --config '$CFG'"
