#!/usr/bin/env python3
"""
Load test for the transcription service.

Modes:
    single     - Single request latency measurement
    concurrent - Concurrent request throughput (use --vus N)
    memory     - Sustained load for memory leak detection

Usage:
    python tests/load/transcription_service.py --mode single
    python tests/load/transcription_service.py --mode concurrent --vus 10
    python tests/load/transcription_service.py --mode memory --vus 2 --duration 300
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package required. Install with: pip install requests")
    sys.exit(1)


DEFAULT_URL = "http://localhost:8080/v1/audio/transcriptions"
DEFAULT_AUDIO = "services/transcription-service/tests/test_audio.wav"
RESULTS_DIR = Path(__file__).parent / "results"


def get_docker_stats():
    """Capture docker stats snapshot."""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format",
             "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"],
            capture_output=True, text=True, timeout=10
        )
        stats = []
        for line in result.stdout.strip().split("\n"):
            if line:
                parts = line.split("\t")
                if len(parts) >= 4:
                    stats.append({
                        "container": parts[0],
                        "cpu": parts[1],
                        "memory": parts[2],
                        "mem_pct": parts[3],
                    })
        return stats
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def send_request(url, audio_path, token):
    """Send a single transcription request. Returns (latency_seconds, status_code, error)."""
    start = time.time()
    try:
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with open(audio_path, "rb") as f:
            resp = requests.post(url, headers=headers, files={"file": f}, timeout=120)
        latency = time.time() - start
        return latency, resp.status_code, None if resp.status_code == 200 else resp.text
    except requests.exceptions.ConnectionError:
        latency = time.time() - start
        return latency, 0, "Connection refused — is the transcription service running?"
    except requests.exceptions.Timeout:
        latency = time.time() - start
        return latency, 0, "Request timed out (120s)"
    except Exception as e:
        latency = time.time() - start
        return latency, 0, str(e)


def compute_percentiles(values):
    """Compute p50, p95, p99 from a list of values."""
    if not values:
        return {"p50": 0, "p95": 0, "p99": 0}
    s = sorted(values)
    n = len(s)
    return {
        "p50": s[int(n * 0.5)],
        "p95": s[int(n * 0.95)] if n >= 20 else s[-1],
        "p99": s[int(n * 0.99)] if n >= 100 else s[-1],
    }


def run_single(url, audio_path, token):
    """Single request baseline."""
    print("Running single request baseline...")
    stats_before = get_docker_stats()
    latency, status, error = send_request(url, audio_path, token)
    stats_after = get_docker_stats()

    result = {
        "mode": "single",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "latency_seconds": round(latency, 3),
        "status_code": status,
        "error": error,
        "docker_stats_before": stats_before,
        "docker_stats_after": stats_after,
    }
    return result


def run_concurrent(url, audio_path, token, vus, iterations=3):
    """Concurrent request throughput."""
    print(f"Running concurrent test with {vus} virtual users, {iterations} iterations...")
    all_latencies = []
    errors = 0
    status_codes = {}
    stats_before = get_docker_stats()

    for i in range(iterations):
        print(f"  Iteration {i+1}/{iterations}...")
        with ThreadPoolExecutor(max_workers=vus) as executor:
            futures = [executor.submit(send_request, url, audio_path, token) for _ in range(vus)]
            for future in as_completed(futures):
                latency, status, error = future.result()
                all_latencies.append(latency)
                status_codes[status] = status_codes.get(status, 0) + 1
                if error:
                    errors += 1

    stats_after = get_docker_stats()
    total = vus * iterations
    percentiles = compute_percentiles(all_latencies)

    result = {
        "mode": "concurrent",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vus": vus,
        "iterations": iterations,
        "total_requests": total,
        "errors": errors,
        "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
        "status_codes": status_codes,
        "latency": percentiles,
        "throughput_rps": round(total / sum(all_latencies) * vus, 3) if all_latencies else 0,
        "docker_stats_before": stats_before,
        "docker_stats_after": stats_after,
    }
    return result


def run_memory(url, audio_path, token, vus, duration):
    """Sustained load for memory leak detection."""
    print(f"Running memory test with {vus} VUs for {duration}s...")
    start_time = time.time()
    all_latencies = []
    errors = 0
    snapshots = []

    snapshots.append({"time": 0, "stats": get_docker_stats()})

    while time.time() - start_time < duration:
        elapsed = time.time() - start_time
        with ThreadPoolExecutor(max_workers=vus) as executor:
            futures = [executor.submit(send_request, url, audio_path, token) for _ in range(vus)]
            for future in as_completed(futures):
                latency, status, error = future.result()
                all_latencies.append(latency)
                if error:
                    errors += 1

        snapshot_time = round(time.time() - start_time, 1)
        snapshots.append({"time": snapshot_time, "stats": get_docker_stats()})
        print(f"  {snapshot_time}s elapsed, {len(all_latencies)} requests completed, {errors} errors")

    percentiles = compute_percentiles(all_latencies)
    total = len(all_latencies)

    result = {
        "mode": "memory",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "vus": vus,
        "duration_seconds": duration,
        "total_requests": total,
        "errors": errors,
        "error_rate": round(errors / total * 100, 2) if total > 0 else 0,
        "latency": percentiles,
        "docker_stats_snapshots": snapshots,
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Transcription service load test")
    parser.add_argument("--mode", choices=["single", "concurrent", "memory"],
                        required=True, help="Test mode")
    parser.add_argument("--vus", type=int, default=5, help="Number of virtual users (default: 5)")
    parser.add_argument("--duration", type=int, default=300,
                        help="Duration in seconds for memory mode (default: 300)")
    parser.add_argument("--url", default=None,
                        help=f"Transcription service URL (default: {DEFAULT_URL})")
    parser.add_argument("--audio", default=None,
                        help=f"Path to test audio file (default: {DEFAULT_AUDIO})")
    parser.add_argument("--token", default=None, help="API token (or set API_TOKEN env var)")
    parser.add_argument("--output", default=None,
                        help="Output file path (default: tests/load/results/<mode>_<timestamp>.json)")
    args = parser.parse_args()

    url = args.url or os.environ.get("TRANSCRIPTION_URL", DEFAULT_URL)
    token = args.token or os.environ.get("API_TOKEN", "")

    # Resolve audio path relative to repo root
    audio_path = args.audio or DEFAULT_AUDIO
    if not os.path.isabs(audio_path):
        # Try relative to script location (tests/load/) -> repo root
        repo_root = Path(__file__).parent.parent.parent
        audio_path = str(repo_root / audio_path)

    if not os.path.exists(audio_path):
        print(f"WARNING: Audio file not found: {audio_path}")
        print("Tests will likely fail with file-not-found errors.")

    if args.mode == "single":
        result = run_single(url, audio_path, token)
    elif args.mode == "concurrent":
        result = run_concurrent(url, audio_path, token, args.vus)
    elif args.mode == "memory":
        result = run_memory(url, audio_path, token, args.vus, args.duration)

    # Output results
    output_json = json.dumps(result, indent=2)
    print("\n--- Results ---")
    print(output_json)

    # Save to file
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = RESULTS_DIR / f"{args.mode}_{timestamp}.json"

    output_path.write_text(output_json)
    print(f"\nResults saved to: {output_path}")

    # Exit with error if there were failures
    if result.get("error") or result.get("errors", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
