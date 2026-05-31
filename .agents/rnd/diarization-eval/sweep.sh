#!/bin/bash
# Parallel param sweep over the diarizer cluster thresholds on a dense clip.
# Runs N configs concurrently (each its own vexaai/vexa-bot:dev container),
# then scores every result against ground truth and prints a ranked table.
set -uo pipefail
REPO="/home/dima/dev/vexa-pack-pack-msteams-diarization-cutover"
EVAL="$REPO/.agents/rnd/diarization-eval"
SAMPLES="$EVAL/samples"
WAV="${1:-IS1009a_dense.wav}"
GT="${2:-IS1009a_dense.gt.json}"
TAG="${WAV%.wav}"
PAR="${PAR:-4}"

# Grid: newSpeakerThreshold x veryFarThreshold x cooldown
NST=(0.30 0.40 0.50)
VFT=(0.45 0.55 0.65)
CD=(1000 4000)
FIXED='"maxUtteranceMs":3000,"pyannoteInferIntervalMs":250'

mkdir -p "$EVAL/sweep_out" "$SAMPLES/sweep_out"
jobs_file="$EVAL/sweep_out/jobs.txt"; : > "$jobs_file"
for nst in "${NST[@]}"; do for vft in "${VFT[@]}"; do for cd in "${CD[@]}"; do
  name="${TAG}_nst${nst}_vft${vft}_cd${cd}"
  cfg="{$FIXED,\"newSpeakerThreshold\":$nst,\"veryFarThreshold\":$vft,\"newClusterCooldownMs\":$cd}"
  echo "$name|$cfg" >> "$jobs_file"
done; done; done
echo "[sweep] $(wc -l < "$jobs_file") configs, parallelism=$PAR, clip=$WAV"

# Slot-pool launch: PAR workers, each pinned to a DISJOINT core range so
# onnxruntime (which sizes its pool from the visible cpuset) self-limits and
# the box doesn't thrash. Worker w owns cores [w*WID, w*WID+WID-1].
NCORES="$(nproc)"
WID=$(( (NCORES - 8) / PAR ))          # leave 8 cores headroom for system
[ "$WID" -lt 4 ] && WID=4
mapfile -t JOBS < "$jobs_file"
echo "[sweep] $PAR workers × ${WID} cores each (of $NCORES)"

worker() {
  local w="$1"; local lo=$(( w * WID )); local hi=$(( lo + WID - 1 ))
  local cpuset="${lo}-${hi}"
  local i="$w"
  while [ "$i" -lt "${#JOBS[@]}" ]; do
    local line="${JOBS[$i]}"; local name="${line%%|*}"; local cfg="${line#*|}"
    CPUSET="$cpuset" bash "$EVAL/run_eval.sh" "$WAV" "sweep_out/${name}.commits.json" "$cfg" "$name" \
        > "$EVAL/sweep_out/${name}.log" 2>&1
    echo "[done w$w cpus$cpuset] $name"
    i=$(( i + PAR ))
  done
}
for w in $(seq 0 $(( PAR - 1 ))); do worker "$w" & done
wait

echo "[sweep] all runs complete — scoring..."
python3 - "$SAMPLES/$GT" "$SAMPLES/sweep_out" "$TAG" <<'PY'
import sys, json, glob, os
gt_path, outdir, tag = sys.argv[1], sys.argv[2], sys.argv[3]
import subprocess
rows=[]
for cf in sorted(glob.glob(os.path.join(outdir, f"{tag}_*.commits.json"))):
    try:
        r=json.loads(subprocess.check_output([
            "python3", os.path.join(os.path.dirname(__file__) or ".", "score.py"),
            "--gt", gt_path, "--commits", cf]))
    except Exception as e:
        # score.py is in EVAL dir
        r=json.loads(subprocess.check_output([
            "python3", os.path.join(os.environ["EVAL"], "score.py"),
            "--gt", gt_path, "--commits", cf]))
    c=r["config"]
    rows.append(dict(nst=c.get("newSpeakerThreshold"), vft=c.get("veryFarThreshold"),
                     cd=c.get("newClusterCooldownMs"), clusters=r["n_clusters"],
                     frame_acc=r["frame_accuracy"], brecall=r["boundary_recall"],
                     bprec=r["boundary_precision"], btime=r["boundary_timing_ms"]))
# objective: frame accuracy primary, boundary recall secondary
rows.sort(key=lambda x:(x["frame_acc"], x["brecall"]), reverse=True)
print(f"\n{'nst':>4} {'vft':>4} {'cd':>5} | {'clust':>5} {'frameAcc':>8} {'bRecall':>7} {'bPrec':>6} {'btime_ms':>8}")
print("-"*60)
for x in rows:
    print(f"{x['nst']:>4} {x['vft']:>4} {x['cd']:>5} | {x['clusters']:>5} {x['frame_acc']:>8.3f} {x['brecall']:>7.3f} {x['bprec']:>6.3f} {str(x['btime']):>8}")
json.dump(rows, open(os.path.join(outdir, f"{tag}.sweep_results.json"),"w"), indent=2)
print(f"\nGT speakers=4. Best (frameAcc then bRecall) on top.")
PY
