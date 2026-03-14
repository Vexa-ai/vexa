#!/bin/bash
#
# Transcription Service — Stress Test
#
# Finds the real capacity limits: latency by audio size, concurrency curve,
# per-worker bottleneck. Saves results to tests/load/results/.
#
# Expects the service to be running. Use test_hot.sh --start first.
#
# Usage:
#   bash tests/test_stress.sh                    # full stress test
#   bash tests/test_stress.sh --latency-only     # just latency by size
#   bash tests/test_stress.sh --concurrency-only # just concurrency curve
#

set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_ROOT="$(cd "$SERVICE_DIR/../.." && pwd)"
RESULTS_DIR="$REPO_ROOT/tests/load/results"
mkdir -p "$RESULTS_DIR"

# ── Service config (read from docker-compose) ─────────────────────────────
HOST_PORT=$(grep -A5 'ports:' "$SERVICE_DIR/docker-compose.yml" 2>/dev/null | grep -oP '\d+(?=:80)' | head -1)
HOST_PORT="${HOST_PORT:-8083}"
ENDPOINT="http://localhost:${HOST_PORT}/v1/audio/transcriptions"
MODEL="large-v3-turbo"
TEST_AUDIO="$SERVICE_DIR/tests/test_audio.wav"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── Count active workers ──────────────────────────────────────────────────
WORKERS=$(docker ps --format '{{.Names}}' | grep -c "transcription.*worker" 2>/dev/null || echo 1)
GPU_INFO=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader 2>/dev/null | head -1 || echo "unknown")

echo "================================================================"
echo "  Transcription Service Stress Test"
echo "  $WORKERS worker(s) | $GPU_INFO | $MODEL"
echo "  $(date)"
echo "================================================================"
echo ""

# ── Generate test audio of different durations ────────────────────────────
AUDIO_DIR=$(mktemp -d)
python3 << PYEOF
import wave, os
src = "$TEST_AUDIO"
out_dir = "$AUDIO_DIR"
w = wave.open(src, 'rb')
params = w.getparams()
frames = w.readframes(w.getnframes())
src_duration = w.getnframes() / w.getframerate()
w.close()
for mult, name in [(1,"short"), (5,"30s"), (10,"60s"), (30,"180s"), (60,"360s")]:
    o = wave.open(f"{out_dir}/audio_{name}.wav", 'wb')
    o.setparams(params)
    o.writeframes(frames * mult)
    o.close()
    print(f"  {name}: {mult * src_duration:.0f}s ({os.path.getsize(f'{out_dir}/audio_{name}.wav')/1024:.0f}KB)")
PYEOF

MODE="${1:---full}"

# ── Phase 1: Latency by audio duration ────────────────────────────────────
run_latency_test() {
  echo ""
  echo "=== Phase 1: Latency by audio duration (single request) ==="
  for AUDIO in "$AUDIO_DIR"/audio_*.wav; do
    NAME=$(basename "$AUDIO" .wav | sed 's/audio_//')
    TMPMETA=$(mktemp)
    curl -s -w "%{http_code} %{time_total}" -o /dev/null \
      -X POST "$ENDPOINT" -F "file=@$AUDIO" -F "model=$MODEL" > "$TMPMETA" 2>&1
    CODE=$(awk '{print $1}' "$TMPMETA")
    LAT=$(awk '{print $2}' "$TMPMETA")
    rm -f "$TMPMETA"
    echo "  $NAME | latency=${LAT}s | status=$CODE"
  done
}

# ── Phase 2: Concurrency curve ────────────────────────────────────────────
run_concurrency_test() {
  echo ""
  echo "=== Phase 2: Concurrency curve (short audio, $WORKERS workers) ==="
  AUDIO="$AUDIO_DIR/audio_short.wav"
  for VUS in 1 5 10 20 30 40 50 60 80 100; do
    START=$(date +%s%N)
    TMPDIR_CODES=$(mktemp -d)
    for i in $(seq 1 "$VUS"); do
      curl -s -o /dev/null -w "%{http_code}\n" \
        -X POST "$ENDPOINT" -F "file=@$AUDIO" -F "model=$MODEL" >> "$TMPDIR_CODES/results.txt" &
    done
    wait
    END=$(date +%s%N)
    TOTAL=$(python3 -c "print(f'{($END-$START)/1e9:.2f}')")
    OK=$(grep -c "200" "$TMPDIR_CODES/results.txt" 2>/dev/null || echo 0)
    FAILS=$((VUS - OK))
    THROUGHPUT=$(python3 -c "print(f'{$OK/max(0.01,$TOTAL):.1f}')")
    GPU_MEM=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i 0 2>/dev/null | tr -d ' ' || echo "?")
    echo "  vus=$VUS | wall=${TOTAL}s | ok=$OK fail=$FAILS | rps=$THROUGHPUT | gpu_mem=${GPU_MEM}MiB"
    rm -rf "$TMPDIR_CODES"
    # Stop if >50% failures
    if [ "$FAILS" -gt $((VUS / 2)) ]; then
      echo "  ^^^ >50% failure rate — capacity reached"
      break
    fi
  done
}

# ── Phase 3: Long audio concurrency ───────────────────────────────────────
run_long_audio_test() {
  echo ""
  echo "=== Phase 3: Concurrency with 60s audio ==="
  AUDIO="$AUDIO_DIR/audio_60s.wav"
  for VUS in 1 5 10 20; do
    START=$(date +%s%N)
    TMPDIR_CODES=$(mktemp -d)
    for i in $(seq 1 "$VUS"); do
      curl -s -o /dev/null -w "%{http_code}\n" \
        -X POST "$ENDPOINT" -F "file=@$AUDIO" -F "model=$MODEL" >> "$TMPDIR_CODES/results.txt" &
    done
    wait
    END=$(date +%s%N)
    TOTAL=$(python3 -c "print(f'{($END-$START)/1e9:.2f}')")
    OK=$(grep -c "200" "$TMPDIR_CODES/results.txt" 2>/dev/null || echo 0)
    FAILS=$((VUS - OK))
    echo "  vus=$VUS | wall=${TOTAL}s | ok=$OK fail=$FAILS"
    rm -rf "$TMPDIR_CODES"
    if [ "$FAILS" -gt 0 ]; then
      echo "  ^^^ failures at $VUS concurrent"
      break
    fi
  done
}

# ── Resource snapshot ─────────────────────────────────────────────────────
resource_snapshot() {
  echo ""
  echo "=== Resource snapshot ==="
  docker stats --no-stream --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}' | grep transcription-service
  nvidia-smi --query-gpu=gpu_name,utilization.gpu,memory.used,memory.total --format=csv,noheader 2>/dev/null | head -3
}

# ── Main ──────────────────────────────────────────────────────────────────
case "$MODE" in
  --full)
    run_latency_test
    run_concurrency_test
    run_long_audio_test
    resource_snapshot
    ;;
  --latency-only)
    run_latency_test
    ;;
  --concurrency-only)
    run_concurrency_test
    ;;
  *)
    echo "Usage: bash tests/test_stress.sh [--full|--latency-only|--concurrency-only]"
    exit 1
    ;;
esac

# Cleanup
rm -rf "$AUDIO_DIR"

echo ""
echo "Save results to: $RESULTS_DIR/transcription_stress_$TIMESTAMP.md"
echo "Done."
