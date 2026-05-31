#!/bin/bash
# Measure CPU + memory overhead of the segmentation+diarization pipeline on a
# fixed core slice, sampling docker stats every second for peak memory + CPU.
set -uo pipefail
REPO="/home/dima/dev/vexa-pack-pack-msteams-diarization-cutover"
EVAL="$REPO/.agents/rnd/diarization-eval"
WAV="${1:-IS1009a_90s.wav}"
CPUSET="${CPUSET:-96-111}"          # 16 dedicated cores
NCPU=$(( $(echo "$CPUSET" | cut -d- -f2) - $(echo "$CPUSET" | cut -d- -f1) + 1 ))
CFG='{"maxUtteranceMs":3000,"newSpeakerThreshold":0.30,"veryFarThreshold":0.45,"newClusterCooldownMs":1000,"pyannoteInferIntervalMs":250,"peekIntervalMs":250}'
NAME="overhead-measure"
LOG="$EVAL/overhead_measure.log"

echo "[measure] cpuset=$CPUSET ($NCPU cores), wav=$WAV"
CPUSET="$CPUSET" bash "$EVAL/run_eval.sh" "$WAV" "${WAV%.wav}.measure.json" "$CFG" "$NAME" > "$LOG" 2>&1 &
RUNPID=$!

# sample docker stats once the container exists
CT="diar-$NAME"
maxmem=0; maxcpu=0; n=0; sumcpu=0
until docker ps --format '{{.Names}}' | grep -q "^$CT$" || ! kill -0 $RUNPID 2>/dev/null; do sleep 0.3; done
while docker ps --format '{{.Names}}' | grep -q "^$CT$"; do
  line=$(docker stats --no-stream --format '{{.MemUsage}}|{{.CPUPerc}}' "$CT" 2>/dev/null) || break
  mem=$(echo "$line" | cut -d'|' -f1 | awk '{print $1}')   # e.g. 1.23GiB
  cpu=$(echo "$line" | cut -d'|' -f2 | tr -d '%')
  # normalize mem to MiB
  memval=$(echo "$mem" | sed -E 's/GiB/*1024/;s/MiB//;s/KiB/\/1024/' | bc -l 2>/dev/null || echo 0)
  awkcmp=$(awk -v a="$memval" -v b="$maxmem" 'BEGIN{print (a>b)?1:0}')
  [ "$awkcmp" = "1" ] && maxmem="$memval"
  cpucmp=$(awk -v a="$cpu" -v b="$maxcpu" 'BEGIN{print (a>b)?1:0}')
  [ "$cpucmp" = "1" ] && maxcpu="$cpu"
  sumcpu=$(awk -v s="$sumcpu" -v c="$cpu" 'BEGIN{print s+c}')
  n=$(( n + 1 ))
  sleep 1
done
wait $RUNPID 2>/dev/null

avgcpu=$(awk -v s="$sumcpu" -v n="$n" 'BEGIN{print (n>0)?s/n:0}')
echo ""
echo "============ OVERHEAD (cpuset=$CPUSET, $NCPU cores) ============"
grep -E "OVERHEAD|DONE" "$LOG" | sed 's/^/  /'
printf "  peak_mem_MiB    : %.0f\n" "$maxmem"
printf "  peak_cpu_pct    : %.0f%%  (=%.1f cores)\n" "$maxcpu" "$(awk -v c="$maxcpu" 'BEGIN{print c/100}')"
printf "  mean_cpu_pct    : %.0f%%  (=%.1f cores)\n" "$avgcpu" "$(awk -v c="$avgcpu" 'BEGIN{print c/100}')"
echo "  samples         : $n"
