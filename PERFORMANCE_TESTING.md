# Vexa Performance Testing Guide

This guide explains how to test the performance of Vexa, especially how the number of vexa-bot containers affects system performance compared to the number of WhisperLive servers.

## Overview

The performance testing system is located in the `vexa-performance-tests/` directory. It allows you to:

1. Run multiple vexa-bots in a Google Meet session 
2. Scale the number of WhisperLive containers
3. Collect metrics on packet loss, latency, and resource usage
4. Track which bot containers communicate with which WhisperLive containers
5. Generate visualizations to find optimal ratio of bots to WhisperLive instances

## Quick Start

```bash
# Navigate to the Vexa directory
cd /path/to/vexa

# Run a performance test
vexa-performance-tests/run_test.sh https://meet.google.com/your-meeting-id
```

## Configuration Options

The test script supports several options:

```bash
vexa-performance-tests/run_test.sh -w 1,3,5 -b 1,5,10,20 -d 180 -s 60 https://meet.google.com/your-meeting-id
```

Options:
- `-w, --whisperlive-counts`: WhisperLive replica counts to test (default: 1,3,5)
- `-b, --bot-counts`: Bot counts to test (default: 1,5,10,20)
- `-d, --duration`: Duration in seconds for each test configuration (default: 180)
- `-s, --stabilize-time`: Time to wait for WhisperLive to stabilize (default: 60)
- `-p, --prefix`: Test prefix for identifying results (default: timestamp)

## Understanding Test Results

The test generates various result files in `vexa-performance-tests/results/`:

- Summary files (JSON format)
- Performance visualizations (PNG format)
- Raw metrics

### Key Metrics

The system tracks:

1. **Packet Loss**: Percentage of audio packets lost between bot and WhisperLive
2. **Processing Failure**: Percentage of received packets that failed processing
3. **Latency**: Time from packet transmission to completed transcription
4. **Container Associations**: Which bots communicated with which WhisperLive instances
5. **Resource Usage**: CPU, memory, and network utilization

### Visualizations

Performance visualizations show:

1. **Heat maps** of packet loss by bot/WhisperLive configuration
2. **Scatter plots** of bot-to-WhisperLive ratio vs. packet loss
3. **Line graphs** showing optimal ratios

## Implementation Details

The system consists of:

1. **Docker containers** for monitoring and metrics collection
2. **Python scripts** for orchestration and analysis
3. **Redis** for real-time data collection

The system works without modifying core Vexa components by monitoring Docker containers and networking.

## Advanced Usage

### Manual Analysis

You can manually analyze results with:

```bash
python vexa-performance-tests/scripts/analyze_results.py --test-prefix "your_test_prefix"
```

### Custom Test Parameters

For custom tests with specific configuration:

```bash
python vexa-performance-tests/scripts/test_orchestrator.py \
  --whisperlive-counts "1,2,3,4,5" \
  --bot-counts "1,3,5,10,15,20" \
  --meeting-url "https://meet.google.com/your-meeting-id" \
  --duration 300
``` 