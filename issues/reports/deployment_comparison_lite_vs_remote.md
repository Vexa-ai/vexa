# Deployment Comparison: Lite vs make all TARGET=remote

## Test Information

| Aspect | Lite Deployment | make all TARGET=remote |
|--------|----------------|--------------------------|
| **Test Date** | December 18, 2025 | December 2, 2025 |
| **Deployment Method** | `docker/lite` (single container) | `make all TARGET=remote` (Docker Compose) |
| **Bot Spawning** | Node.js child processes | Docker containers |
| **Architecture** | All services in one container | Multiple containers orchestrated |
| **Transcription** | Remote API (default) | Remote API (Fireworks.ai) |

---

## Architecture Differences

### Lite Deployment
- **Container Model:** Single container with all services
- **Bot Execution:** Process orchestrator spawns bots as Node.js child processes
- **Services Included:** API Gateway, Admin API, Bot Manager, Transcription Collector, WhisperLive, Redis (internal)
- **Docker Socket:** Not required
- **Isolation:** Process-level (less isolated)
- **Use Case:** Platforms without Docker socket access (EasyPanel, Dokploy, Railway, Render)

### make all TARGET=remote
- **Container Model:** Multiple containers via Docker Compose
- **Bot Execution:** Each bot runs in separate Docker container
- **Services:** Separate containers for API Gateway, Admin API, Bot Manager, Transcription Collector, WhisperLive-remote, Redis, PostgreSQL, Traefik, Consul
- **Docker Socket:** Required
- **Isolation:** Container-level (more isolated)
- **Use Case:** Full Docker Compose deployment with remote transcription backend

---

## Hardware Configuration

| Component | Lite Deployment | make all TARGET=remote |
|-----------|----------------|--------------------------|
| **CPU** | Not specified in report | AMD EPYC 7713 64-Core Processor (6 vCPUs allocated) |
| **RAM** | Not specified in report | 16 GB |
| **Disk** | Not specified in report | 315 GB SSD |
| **OS** | Not specified in report | Linux 6.8.0-71-generic |

---

## Tested Bot Counts

| Deployment | Bot Counts Tested | Maximum Tested |
|------------|-------------------|---------------|
| **Lite** | 4, 8, 12 bots | 12 bots |
| **make all TARGET=remote** | 4, 8, 12, 16 bots | 16 bots |

---

## Observed Resource Usage Data

### Lite Deployment

| Bots | Total RAM | Chrome RAM | CPU % | Load Avg |
|------|-----------|------------|-------|----------|
| 4 | 8.0 GB | 1.0 GB | 39% | 2.11 |
| 8 | 9.4 GB | 1.8 GB | 59% | 8.91 |
| 12 | 12.4 GB | 2.5 GB | 87% | 21.99 |

### make all TARGET=remote

| Bots | Total RAM | Memory (GB) | Load Avg | CPU Utilization | CPU Idle |
|------|-----------|-------------|----------|-----------------|----------|
| 4 | 10 GB (63%) | 10 | 22.89 | Not specified | 22% |
| 8 | ~11 GB (69%) | ~11 | ~45 | Not specified | ~10% |
| 12 | ~12 GB (75%) | ~12 | ~65 | Not specified | ~2% |
| 16 | 13 GB (81%) | 13 | 69.80 | 98.2% | 0% |

---

## Per-Bot Resource Usage

### Lite Deployment

| Resource | Per Bot | Notes |
|---------|---------|-------|
| **RAM** | 0.35 GB | Base system: 7.0 GB |
| **CPU** | 10-15% of 1 core | Minimum: 10%, Recommended: 15% |
| **Chrome RAM** | 0.25 GB | Per bot instance |
| **Load Average** | Non-linear growth | ~2x per bot doubling due to system overhead |

### make all TARGET=remote

| Resource | Per Bot | Notes |
|---------|---------|-------|
| **RAM** | ~600 MB | Plus ~800 MB virtual memory |
| **CPU** | ~24% of 1 core | ~4-5 load average points per bot |
| **Chrome RAM** | ~600 MB | 9 processes per bot (1 browser + 1 renderer + 1 GPU + 6 utility) |
| **Total Memory** | ~1.4 GB | RAM + virtual memory combined |
| **Chrome Processes** | 9 processes | Per bot |

---

## Resource Scaling Formulas

### Lite Deployment

**Memory Formula:**
```
RAM (GB) = 7.0 + (0.35 × bot_count)
Recommended RAM (GB) = [7.0 + (0.35 × bot_count)] × 1.2
```

**CPU Formula:**
```
CPU Cores (minimum) = bot_count × 0.10
CPU Cores (recommended) = bot_count × 0.15
```

**Chrome Memory Formula:**
```
Chrome RAM (GB) = bot_count × 0.25
```

**Swap Formula:**
```
Swap (GB) = RAM × 0.5
```

### make all TARGET=remote

**Resource Calculation Formula:**
```
Required Cores = (Target Bots × 0.24) × 1.5  (50% headroom)
Required RAM (GB) = (Target Bots × 0.6) × 1.5  (50% headroom)
```

**Scaling Pattern:**
- Each additional bot adds ~4-5 points to load average
- Each additional bot consumes ~200-300 MB RAM
- Linear to sub-linear scaling

---

## Resource Comparison at Different Bot Counts

### At 4 Bots

| Metric | Lite | make all TARGET=remote | Difference |
|--------|------|----------------|------------|
| **Total RAM** | 8.0 GB | 10 GB | +2.0 GB (25% more) |
| **Chrome RAM** | 1.0 GB | Not specified | - |
| **CPU %** | 39% | Not specified | - |
| **Load Avg** | 2.11 | 22.89 | +20.78 (10.8x higher) |
| **CPU Idle** | Not specified | 22% | - |

### At 8 Bots

| Metric | Lite | make all TARGET=remote | Difference |
|--------|------|----------------|------------|
| **Total RAM** | 9.4 GB | ~11 GB | +1.6 GB (17% more) |
| **Chrome RAM** | 1.8 GB | Not specified | - |
| **CPU %** | 59% | Not specified | - |
| **Load Avg** | 8.91 | ~45 | +36.09 (5.1x higher) |
| **CPU Idle** | Not specified | ~10% | - |

### At 12 Bots

| Metric | Lite | make all TARGET=remote | Difference |
|--------|------|----------------|------------|
| **Total RAM** | 12.4 GB | ~12 GB | -0.4 GB (3% less) |
| **Chrome RAM** | 2.5 GB | Not specified | - |
| **CPU %** | 87% | Not specified | - |
| **Load Avg** | 21.99 | ~65 | +43.01 (3.0x higher) |
| **CPU Idle** | Not specified | ~2% | - |

---

## Process Distribution

### Lite Deployment
- **Process Model:** Node.js child processes within single container
- **Isolation:** Process-level
- **Overhead:** Lower (no Docker container overhead per bot)

### make all TARGET=remote (at 16 bots)

| Process Type | Count | Total CPU% | Total Memory | Per Bot |
|-------------|-------|------------|--------------|---------|
| **Chrome (Playwright)** | 144 | 515.3% | 13.9 GB | ~24% CPU, ~600 MB RAM |
| **Node.js (Bots)** | 16 | 32.9% | 1.9 GB | ~2% CPU, ~120 MB RAM |
| **Xvfb (Display)** | 16 | 8.1% | 896 MB | ~0.5% CPU, ~56 MB RAM |
| **Load Test Server** | 1 | 30.1% | 791 MB | N/A |
| **Other Services** | ~20 | 20.0% | ~2 GB | N/A |

**Total Chrome Processes:** 144 processes for 16 bots (9 processes per bot)

---

## Base System Memory

| Deployment | Base System RAM | Per-Bot RAM | Formula |
|------------|----------------|-------------|---------|
| **Lite** | 7.0 GB | 0.35 GB | RAM = 7.0 + (0.35 × bots) |
| **make all TARGET=remote** | Not explicitly stated | ~0.6 GB | At 4 bots: 10 GB total |

---

## Load Average Comparison

| Bots | Lite Load Avg | make all TARGET=remote Load Avg | Ratio |
|------|---------------|------------------------|-------|
| 4 | 2.11 | 22.89 | 10.8x higher |
| 8 | 8.91 | ~45 | 5.1x higher |
| 12 | 21.99 | ~65 | 3.0x higher |

**Observation:** make all TARGET=remote shows significantly higher load averages at all bot counts, with the gap decreasing as bot count increases.

---

## Chrome Memory Usage

| Deployment | Chrome RAM per Bot | Notes |
|------------|-------------------|-------|
| **Lite** | 0.25 GB | Linear scaling |
| **make all TARGET=remote** | ~0.6 GB | 9 processes per bot (browser + renderer + GPU + utilities) |

**Difference:** make all TARGET=remote uses 2.4x more Chrome memory per bot.

---

## CPU Usage Patterns

### Lite Deployment
- **Per Bot:** 10-15% of one core
- **At 4 bots:** 39% total CPU
- **At 8 bots:** 59% total CPU
- **At 12 bots:** 87% total CPU
- **Scaling:** Linear

### make all TARGET=remote
- **Per Bot:** ~24% of one core
- **At 16 bots:** 98.2% CPU utilization
- **Load Average:** 69.80 (11.6x capacity with 6 cores)
- **Scaling:** Linear to sub-linear

**Difference:** make all TARGET=remote uses approximately 1.6-2.4x more CPU per bot.

---

## Maximum Capacity

| Deployment | Maximum Tested | Maximum Capacity | Notes |
|------------|----------------|------------------|-------|
| **Lite** | 12 bots | Not specified in report | Recommended max: 3-5 bots (per documentation) |
| **make all TARGET=remote** | 16 bots | 16 bots (at resource limits) | System at maximum practical capacity |

---

## Resource Bottlenecks

### Lite Deployment
- **At 12 bots:** 87% CPU utilization
- **Memory:** 12.4 GB total (base 7.0 GB + 5.4 GB for bots)
- **Load Average:** 21.99 at 12 bots

### make all TARGET=remote
- **Primary Bottleneck:** CPU (load average 69.80, 11.6x capacity)
- **Secondary Bottleneck:** Memory (81% RAM + 99% swap at 16 bots)
- **At 16 bots:** 98.2% CPU utilization, 0% CPU idle
- **Swap:** 495 MB used (99.9% of 496 MB total)

---

## Scaling Characteristics

### Lite Deployment
1. **Memory scales linearly:** +0.35 GB per bot (base 7 GB)
2. **CPU scales linearly:** ~15% of one core per bot
3. **Chrome memory scales linearly:** ~0.25 GB per bot instance
4. **Load average grows non-linearly:** System overhead compounds with more bots (~2x per bot doubling)

### make all TARGET=remote
1. **Memory scales linearly to sub-linearly:** ~200-300 MB per bot
2. **CPU scales linearly:** ~24% of one core per bot (~4-5 load points)
3. **Chrome memory:** ~600 MB per bot (9 processes)
4. **Load average:** Linear to sub-linear growth

---

## Quick Reference: Resource Planning

### Lite Deployment

| Bots | RAM (GB) | CPU Cores | Swap (GB) |
|------|----------|-----------|-----------|
| 4 | 10 | 1 | 5 |
| 8 | 12 | 2 | 6 |
| 12 | 16 | 2-3 | 8 |
| 16 | 18 | 3 | 9 |
| 20 | 21 | 3-4 | 11 |

*Values include 20% safety buffer for RAM, recommended CPU cores, and 50% swap.*

### make all TARGET=remote

| Bots | RAM (GB) | CPU Cores | Notes |
|------|----------|-----------|-------|
| 16 | 24 | 8-12 | 50% headroom |
| 32 | 48 | 16-20 | 2x current capacity |
| 64 | 96 | 32-40 | 4x current capacity |

---

## Test Duration and Stability

| Deployment | Test Duration | Bot Crashes | Stability |
|------------|---------------|-------------|-----------|
| **Lite** | Not specified | Not specified | Not specified |
| **make all TARGET=remote** | 1 hour 26 minutes | 0 crashes | All 16 bots operational |

---

## Deployment Requirements

### Lite Deployment
- **Docker Socket:** Not required
- **External Services:** PostgreSQL (required), Redis (optional, internal default)
- **Transcription:** Remote API (default) or local CPU mode
- **Platforms:** EasyPanel, Dokploy, Railway, Render, etc.

### make all TARGET=remote
- **Docker Socket:** Required
- **External Services:** All services in Docker Compose (PostgreSQL, Redis, etc.)
- **Transcription:** Remote API (Fireworks.ai)
- **Platforms:** Full Docker Compose environment

---

## Summary of Key Differences

1. **Resource Efficiency:** Lite deployment uses significantly less resources per bot (0.35 GB RAM vs 0.6 GB RAM, 10-15% CPU vs 24% CPU)

2. **Load Average:** make all TARGET=remote shows 3-10x higher load averages at equivalent bot counts

3. **Architecture:** Lite uses process-based bot spawning (lower overhead), make all TARGET=remote uses container-based (higher isolation, higher overhead)

4. **Maximum Capacity:** make all TARGET=remote tested up to 16 bots, Lite tested up to 12 bots (documentation recommends 3-5 for Lite)

5. **Base System Memory:** Lite has explicit 7.0 GB base, make all TARGET=remote base not explicitly stated

6. **Chrome Memory:** Lite uses 0.25 GB per bot, make all TARGET=remote uses 0.6 GB per bot (2.4x difference)

7. **CPU Usage:** Lite uses 10-15% CPU per bot, make all TARGET=remote uses ~24% CPU per bot (1.6-2.4x difference)

---

**Report Generated:** Based on load test reports from December 2, 2025 (make all TARGET=remote) and December 18, 2025 (Lite)

