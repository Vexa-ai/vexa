#!/bin/bash
# Fast clustering sweep: capture pyannote+wespeaker embeddings ONCE, then
# replay the clustering grid in milliseconds. Iteration time ≈ capture time
# (~once) + sub-second replay for the whole grid.
set -uo pipefail
REPO="/home/dima/dev/vexa-pack-pack-msteams-diarization-cutover"
CORE="$REPO/services/vexa-bot/core"
EVAL="$REPO/.agents/rnd/diarization-eval"
SAMPLES="$EVAL/samples"
WAV="${1:-IS1009a_30s.wav}"
GT="${2:-IS1009a_30s.gt.json}"
TAG="${WAV%.wav}"
EMB="$SAMPLES/${TAG}.capture.emb.json"
mkdir -p "$SAMPLES/sweep_out"

docker_run() {  # $1=extra-env $2=cmd
  docker run --rm --cpuset-cpus="${CPUSET:-0-63}" \
    -v "$CORE/src:/app/vexa-bot/core/src:ro" \
    -v "$CORE/eval-diarizer.ts:/app/vexa-bot/core/eval-diarizer.ts:ro" \
    -v "$CORE/replay-clustering.ts:/app/vexa-bot/core/replay-clustering.ts:ro" \
    -v "$SAMPLES:/data" -v "diar-hfcache:/hfcache" \
    -e DIAR_CACHE=/hfcache $1 -w /app/vexa-bot/core --entrypoint bash \
    vexaai/vexa-bot:dev -c "$2"
}

# 1. CAPTURE (once) — full pipeline, dump embeddings. Skip if cached.
if [ ! -f "$EMB" ] || [ "${FORCE_CAPTURE:-0}" = "1" ]; then
  echo "[fast] capturing embeddings for $WAV (full pipeline, one-time)..."
  CAPCFG='{"maxUtteranceMs":3000,"pyannoteInferIntervalMs":250}'
  docker_run "-e DIAR_DUMP_EMB=1" \
    "npx tsx eval-diarizer.ts --wav /data/$WAV --out /data/${TAG}.capture.json --config '$CAPCFG'" \
    2>&1 | grep -E "\[eval\] (DONE|wrote.*emb|OVERHEAD)" | sed 's/^/  /'
else
  echo "[fast] reusing cached $EMB"
fi
NUTT=$(python3 -c "import json;print(len(json.load(open('$EMB'))['records']))")
echo "[fast] $NUTT cached utterances"

# 2. Build config grid
python3 - "$SAMPLES/sweep_out" "$TAG" <<'PY'
import json, sys, os
outdir, tag = sys.argv[1], sys.argv[2]
NST=[0.25,0.30,0.35,0.40,0.45,0.50]
VFT=[0.40,0.45,0.55,0.65]
CD=[0,1000,4000]
jobs=[]
for nst in NST:
  for vft in VFT:
    if vft<=nst: continue
    for cd in CD:
      name=f"{tag}_nst{nst}_vft{vft}_cd{cd}"
      jobs.append(dict(name=name, config=dict(newSpeakerThreshold=nst,veryFarThreshold=vft,newClusterCooldownMs=cd),
                       out=f"/data/sweep_out/{name}.commits.json"))
json.dump(jobs, open(os.path.join(outdir,f"{tag}.configs.json"),"w"))
print(f"[fast] {len(jobs)} configs")
PY

# 3. REPLAY all configs in ONE container (sub-second)
echo "[fast] replaying grid..."
docker_run "" "npx tsx replay-clustering.ts --emb /data/${TAG}.capture.emb.json --configs /data/sweep_out/${TAG}.configs.json" \
  2>&1 | grep -E "replayed in|configs" | sed 's/^/  /'

# 4. Score all on host
echo "[fast] scoring..."
python3 - "$SAMPLES/$GT" "$SAMPLES/sweep_out" "$TAG" "$EVAL" <<'PY'
import sys, json, glob, os, subprocess
gt, outdir, tag, evaldir = sys.argv[1:5]
rows=[]
for cf in glob.glob(os.path.join(outdir, f"{tag}_*.commits.json")):
    r=json.loads(subprocess.check_output(["python3", os.path.join(evaldir,"score.py"), "--gt", gt, "--commits", cf]))
    c=r["config"]
    rows.append(dict(nst=c.get("newSpeakerThreshold"),vft=c.get("veryFarThreshold"),cd=c.get("newClusterCooldownMs"),
                     clusters=r["n_clusters"],frame_acc=r["frame_accuracy"],brecall=r["boundary_recall"],
                     bprec=r["boundary_precision"],btime=r["boundary_timing_ms"]))
rows.sort(key=lambda x:(x["frame_acc"],x["brecall"]),reverse=True)
print(f"\nGT=4 speakers. Top by frameAcc then bRecall (of {len(rows)} configs):")
print(f"{'nst':>4} {'vft':>4} {'cd':>5} | {'clust':>5} {'frameAcc':>8} {'bRecall':>7} {'bPrec':>6} {'btime':>6}")
print("-"*58)
for x in rows[:15]:
    print(f"{x['nst']:>4} {x['vft']:>4} {x['cd']:>5} | {x['clusters']:>5} {x['frame_acc']:>8.3f} {x['brecall']:>7.3f} {x['bprec']:>6.3f} {str(x['btime']):>6}")
json.dump(rows, open(os.path.join(outdir,f"{tag}.fast_results.json"),"w"),indent=2)
PY
echo "[fast] done."
