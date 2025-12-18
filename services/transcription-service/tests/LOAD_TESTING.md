# Load Testing Guide

This guide explains how to use the load testing tools to measure transcription service performance and scaling behavior.

## Overview

The load testing suite measures:
1. **Single Worker Max Performance** - Find the maximum throughput of a single worker
2. **Multi-Worker Scaling** - Measure how performance scales with multiple workers

## Quick Start

### Run All Tests
```bash
cd /home/dima/dev/vexa-transcription-service
./tests/run_load_test.sh all
```

This will:
1. Test single worker max performance (finds optimal concurrency)
2. Test scaling with 1, 2, and 3 workers
3. Generate a scaling summary showing efficiency

### Test Single Worker Only
```bash
./tests/run_load_test.sh single
```

### Test Scaling Only
```bash
./tests/run_load_test.sh scaling
```

### Custom Scaling Test
```bash
./tests/run_load_test.sh scaling '1 2 3' 30 8
# Tests with 1, 2, 3 workers
# 30 requests per worker
# Concurrency level 8
```

## Manual Usage

### Scale Workers Manually
```bash
./tests/scale_workers.sh 1  # Scale to 1 worker
./tests/scale_workers.sh 2  # Scale to 2 workers
./tests/scale_workers.sh 3  # Scale to 3 workers
```

### Run Load Test Directly
```bash
# Test single worker max performance
python3 tests/load_test.py \
    --test-single \
    --worker-url http://localhost:8000 \
    --audio-file tests/test_audio.wav

# Test scaling with specific parameters
python3 tests/load_test.py \
    --test-scaling \
    --api-url http://localhost:8083 \
    --workers 1 2 3 \
    --requests 20 \
    --concurrency 4
```

## Metrics Explained

### Requests per Second (RPS)
- **Definition**: Number of transcription requests processed per second
- **Higher is better**: Indicates higher throughput
- **Typical values**: 5-20 RPS per worker (depends on audio length and model)

### Response Time (Latency)
- **Min/Max/Avg**: Minimum, maximum, and average response times
- **P50/P95/P99**: Percentiles (50th, 95th, 99th)
  - P50 (median): Typical response time
  - P95: 95% of requests complete within this time
  - P99: 99% of requests complete within this time
- **Lower is better**: Faster responses

### Real-Time Factor (RT Factor)
- **Definition**: Audio duration / Processing time
- **Example**: If 5 seconds of audio is processed in 0.5 seconds, RT Factor = 10x
- **Higher is better**: Indicates faster-than-real-time processing
- **Typical values**: 10-20x for GPU with large-v3-turbo + INT8

### Throughput (Audio Duration/sec)
- **Definition**: Total audio duration processed per second of wall-clock time
- **Example**: If 100 seconds of audio is processed in 5 seconds, throughput = 20x
- **Higher is better**: More audio processed per unit time

### Success/Error Rate
- **Success Rate**: Percentage of requests that completed successfully
- **Error Rate**: Percentage of requests that failed
- **Target**: >95% success rate for production

### Scaling Efficiency
- **Definition**: Actual RPS / (Expected RPS = Base RPS × Worker Count)
- **Example**: If 1 worker = 10 RPS, expected for 3 workers = 30 RPS
  - If actual = 28 RPS, efficiency = 93.3%
- **Target**: >90% efficiency indicates good scaling

## Interpreting Results

### Single Worker Max Performance

The script tests different concurrency levels (1, 2, 4, 8, 16, 32, 64) to find:
- **Optimal concurrency**: The concurrency level that gives maximum RPS
- **Max RPS**: Maximum requests per second achievable

**Example Output:**
```
Optimal Concurrency: 8
Max RPS: 12.45
```

This means:
- With concurrency=8, the worker can handle 12.45 requests/second
- Higher concurrency may cause errors or not improve performance

### Multi-Worker Scaling

The script tests different worker counts and shows:
- How RPS scales with worker count
- Whether scaling is linear (efficient) or sub-linear (bottlenecks)

**Example Output:**
```
Workers    RPS             Avg Latency     RT Factor       Success Rate   
----------------------------------------------------------------------
1          10.50           0.476           15.20           100.00         %
2          20.10           0.498           14.95           100.00         %
3          29.80           0.512           14.80           100.00         %

Scaling Efficiency:
  2 workers: 20.10 RPS (expected: 21.00, efficiency: 95.7%)
  3 workers: 29.80 RPS (expected: 31.50, efficiency: 94.6%)
```

**Interpretation:**
- Linear scaling: RPS doubles/triples with workers (efficient)
- Sub-linear scaling: RPS increases but not proportionally (bottlenecks)
- Common bottlenecks: GPU memory, network, load balancer, shared resources

## Best Practices

### 1. Warm-up Period
- Workers need time to load models and initialize
- Wait 30-60 seconds after scaling before testing
- The script includes automatic wait times

### 2. Test Duration
- Use enough requests to get statistically significant results
- Minimum: 20 requests per worker
- Recommended: 50-100 requests per worker for accurate metrics

### 3. Concurrency Levels
- Start with low concurrency (2-4) for scaling tests
- Use higher concurrency (8-16) for max performance tests
- Monitor error rates - if >5%, reduce concurrency

### 4. Audio File Selection
- Use representative audio files (similar to production)
- Test with different audio lengths
- Current test uses ~5.5 seconds of audio

### 5. Resource Monitoring
- Monitor GPU memory usage during tests
- Check worker logs for errors
- Monitor system CPU/memory if testing CPU mode

## Troubleshooting

### All Requests Failing (405 Method Not Allowed)
- **Cause**: Wrong endpoint URL
- **Fix**: Ensure URL includes `/v1/audio/transcriptions`
- **Check**: The script auto-appends this, but verify the base URL

### High Error Rate (>10%)
- **Cause**: Concurrency too high, workers overloaded
- **Fix**: Reduce concurrency level
- **Check**: Worker logs for specific errors

### 502 Bad Gateway
- **Cause**: Workers not ready or crashed
- **Fix**: Wait longer for workers to start, check health
- **Check**: `docker logs transcription-worker-1`

### Low Scaling Efficiency (<80%)
- **Cause**: Bottleneck (GPU memory, network, load balancer)
- **Investigate**: 
  - Check GPU memory usage (should be <80% per worker)
  - Check nginx logs for load balancer issues
  - Verify workers are on different GPUs (if available)

### Inconsistent Results
- **Cause**: System load, other processes, cold starts
- **Fix**: 
  - Run multiple test iterations
  - Ensure system is idle
  - Wait for workers to fully warm up

## Example Test Scenarios

### Scenario 1: Find Max Single Worker Performance
```bash
./tests/run_load_test.sh single
```
**Goal**: Determine optimal concurrency and max RPS for a single worker

### Scenario 2: Validate Scaling to 3 Workers
```bash
./tests/run_load_test.sh scaling '1 2 3' 50 4
```
**Goal**: Verify that 3 workers provide ~3x the throughput of 1 worker

### Scenario 3: Stress Test
```bash
python3 tests/load_test.py \
    --test-scaling \
    --workers 3 \
    --requests 100 \
    --concurrency 16 \
    --timeout 300
```
**Goal**: Test system under high load, find breaking point

### Scenario 4: Production Simulation
```bash
# Simulate production: 3 workers, moderate load
python3 tests/load_test.py \
    --test-scaling \
    --workers 3 \
    --requests 200 \
    --concurrency 8
```
**Goal**: Test realistic production workload

## Requirements

- Python 3.7+
- `aiohttp` library: `pip install aiohttp`
- Docker and docker-compose (for scaling workers)
- Test audio file: `tests/test_audio.wav` (auto-generated)

## Files

- `load_test.py` - Main load testing script
- `run_load_test.sh` - Helper script for common test scenarios
- `scale_workers.sh` - Helper script to scale workers
- `test_audio.wav` - Test audio file (~5.5 seconds, 187 KB)









