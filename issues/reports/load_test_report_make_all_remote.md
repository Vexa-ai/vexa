# Vexa Bot Scaling Report - Remote Backend Configuration

## Executive Summary

**Test Date:** December 2, 2025 14:21:39 UTC  
**Deployment:** `make all TARGET=remote` (Docker Compose with remote transcription API)  
**Hardware:** 6 vCPUs, 16 GB RAM  
**Maximum Capacity:** **16 concurrent bots** (at resource limits)  
**Test Status:** ✅ PASS - All 16 bots operational, system at capacity

---

## 🎯 Key Findings: Bot Scaling Capacity

### Maximum Sustained Capacity
- **16 concurrent bots** successfully deployed and running
- **100% deployment success rate** (16/16 bots)
- **Zero bot crashes** during 1h 26min test period
- System operating at **maximum practical capacity** for this hardware

### Per-Bot Resource Usage
| Resource | Per Bot | Notes |
|----------|---------|-------|
| **CPU** | ~24% of 1 core | ~4-5 load average points per bot |
| **Memory** | ~600 MB RAM | Plus ~800 MB virtual memory |
| **Chrome Processes** | 9 processes | 1 browser + 1 renderer + 1 GPU + 6 utility |
| **Total Memory** | ~1.4 GB | RAM + virtual memory combined |

### Scaling Characteristics
```
Bots:    4 →  8 → 12 → 16
Load:   22 → 45 → 65 → 70
Memory: 10 → 11 → 12 → 13 GB
```

**Scaling Pattern:** Linear to sub-linear
- Each additional bot adds ~4-5 points to load average
- Each additional bot consumes ~200-300 MB RAM
- Resource usage is predictable and consistent

---

## 📊 Resource Utilization at 16 Bots

### CPU (Primary Bottleneck) 🔴
| Metric | Value | Status |
|--------|-------|--------|
| **Load Average** | 69.80 | 🔴 CRITICAL (11.6x capacity) |
| **CPU Utilization** | 98.2% | 🔴 CRITICAL |
| **CPU Idle** | 0.0% | 🔴 CRITICAL |
| **Normalized Load** | 1163% | 🔴 CRITICAL |

**Analysis:** System operating at 11.6x nominal capacity. With 6 cores, ideal load should be < 6.0, but currently at 69.80. This is the **primary limiting factor** for scaling.

### Memory (Secondary Bottleneck) 🟡→🔴
| Metric | Value | Status |
|--------|-------|--------|
| **Used RAM** | 13 GB (81%) | 🟡 WARNING |
| **Available RAM** | 1.6 GB (10%) | 🟡 WARNING |
| **Swap Used** | 495 MB (99.9%) | 🔴 CRITICAL |
| **Free Swap** | 368 KB (0.1%) | 🔴 CRITICAL |

**Analysis:** Memory pressure is severe. 99.9% swap utilization indicates RAM exhaustion. Only 1.6 GB available for new allocations. High risk of OOM killer activation.

### Disk I/O ✅
| Metric | Value | Status |
|--------|-------|--------|
| **Disk Usage** | 7% | ✅ HEALTHY |
| **Disk Utilization** | 1.49% | ✅ HEALTHY |

**Analysis:** Disk I/O is not a bottleneck. Plenty of storage space and low I/O wait.

---

## 📈 Scaling Analysis

### Resource Growth Pattern
| Bots | Load Avg | Memory (GB) | Swap (MB) | CPU Idle | Status |
|------|----------|-------------|-----------|----------|--------|
| 4 | 22.89 | 10 (63%) | 1 (<1%) | 22% | 🟢 MODERATE |
| 8 | ~45 | ~11 (69%) | ~200 (40%) | ~10% | 🟡 HIGH |
| 12 | ~65 | ~12 (75%) | ~400 (81%) | ~2% | 🟡 HIGH |
| 16 | 69.80 | 13 (81%) | 495 (99%) | 0% | 🔴 CRITICAL |

**Key Observations:**
- Load increased 3x in first 3 minutes (22 → 66) when scaling from 4 to 16 bots
- Memory consumption plateaued at 13 GB (81% of 16 GB)
- Swap maxed out within 4 minutes and stayed at 99%
- System stabilized around load 70 (still critical but operational)

### Process Distribution at 16 Bots
| Process Type | Count | Total CPU% | Total Memory | Per Bot |
|-------------|-------|------------|--------------|---------|
| **Chrome (Playwright)** | 144 | 515.3% | 13.9 GB | ~24% CPU, ~600 MB RAM |
| **Node.js (Bots)** | 16 | 32.9% | 1.9 GB | ~2% CPU, ~120 MB RAM |
| **Xvfb (Display)** | 16 | 8.1% | 896 MB | ~0.5% CPU, ~56 MB RAM |
| **Load Test Server** | 1 | 30.1% | 791 MB | N/A |
| **Other Services** | ~20 | 20.0% | ~2 GB | N/A |

**Analysis:** Chrome processes are the dominant resource consumer, accounting for 5.15 cores (515% CPU) and 13.9 GB memory. This is the primary scaling constraint.

---

## 🔍 Capacity Limits & Bottlenecks

### 1. CPU Bottleneck (PRIMARY LIMITER) 🔴
- **Current State:** Load average 69.80 with 6 cores (11.6x capacity)
- **Impact:** Zero idle CPU cycles, severe CPU contention
- **Per-Bot Cost:** ~24% CPU (~4-5 load points)
- **Maximum Capacity:** 16 bots on 6-core system (at limit)

### 2. Memory Bottleneck (SECONDARY LIMITER) 🟡→🔴
- **Current State:** 81% RAM + 99% swap utilization
- **Impact:** High risk of OOM killer, performance degradation from swapping
- **Per-Bot Cost:** ~600 MB RAM + ~800 MB virtual
- **Maximum Capacity:** 16 bots on 16 GB system (at limit)

### 3. Chrome Process Overhead 🟡
- **Current State:** 144 Chrome processes for 16 bots (9 per bot)
- **Impact:** High process management and context switching overhead
- **Per-Bot Cost:** 9 processes, ~600 MB RAM, ~24% CPU
- **Note:** This is inherent to browser automation architecture

---

## 💡 Capacity Planning Recommendations

### Current Hardware (6 cores, 16 GB RAM)
- **Maximum Capacity:** 16 bots (at resource limits)
- **Recommended Production:** 8-10 bots (for safety margin)
- **Status:** Operating at maximum practical capacity

### Scaling Guidelines

| Target Bots | Recommended Hardware | Notes |
|------------|---------------------|-------|
| **16 bots** | 8-12 cores, 24 GB RAM | 50% headroom for safety |
| **32 bots** | 16-20 cores, 48 GB RAM | 2x current capacity |
| **64 bots** | 32-40 cores, 96 GB RAM | 4x current capacity |
| **100+ bots** | Distributed architecture | Multiple servers required |

### Resource Calculation Formula
```
Required Cores = (Target Bots × 0.24) × 1.5  (50% headroom)
Required RAM (GB) = (Target Bots × 0.6) × 1.5  (50% headroom)
```

**Example for 32 bots:**
- Cores: (32 × 0.24) × 1.5 = 11.5 → **12-16 cores**
- RAM: (32 × 0.6) × 1.5 = 28.8 → **32 GB RAM**

---

## 🎯 Test Configuration

### Deployment
- **Method:** Docker Compose via `make all TARGET=remote`
- **Profile:** `remote` (whisperlive-remote service with `--backend remote`)
- **Transcription:** Remote API (Fireworks.ai) - offloads transcription computation

### Test Setup
- **Total Bots:** 16 concurrent bots
- **Bot Groups:** 4 initial bots + 12 added incrementally
- **Meetings:** 2 Google Meet rooms (~8 bots per room)
- **Duration:** 1 hour 26 minutes
- **Language:** English
- **Task:** Transcription

### Hardware Under Test
| Component | Specification |
|-----------|---------------|
| **CPU** | AMD EPYC 7713 64-Core Processor (6 vCPUs allocated) |
| **RAM** | 16 GB |
| **Disk** | 315 GB SSD |
| **OS** | Linux 6.8.0-71-generic |

---

## ✅ Test Results Summary

### Success Criteria
| Criteria | Result | Status |
|----------|--------|--------|
| Deploy 16 concurrent bots | 16/16 bots | ✅ PASS |
| All bots join meetings | 100% success | ✅ PASS |
| No bot crashes | 0 crashes | ✅ PASS |
| System remains operational | Stable (under stress) | ✅ PASS |
| Identify capacity limits | CPU + Memory identified | ✅ PASS |

**Overall Result:** ✅ **PASS** - System successfully sustained 16 concurrent bots at maximum capacity

---

## 📝 Key Takeaways

1. **Maximum Capacity Validated:** 16 concurrent bots is the practical limit for 6-core/16GB hardware
2. **Scaling is Predictable:** Linear resource usage per bot (~24% CPU, ~600 MB RAM)
3. **Primary Limiter:** CPU (load average 11.6x capacity)
4. **Secondary Limiter:** Memory (81% RAM + 99% swap)
5. **Stability:** All bots remained operational despite extreme resource pressure
6. **Remote Backend Advantage:** Transcription computation offloaded to external API reduces local CPU load

---

## 🔧 Production Recommendations

### For 16 Bots
- **Minimum:** 6 cores, 16 GB RAM (current test - at limit)
- **Recommended:** 8-12 cores, 24 GB RAM (50% headroom)
- **Swap Space:** Increase to 4-8 GB (currently only 496 MB)

### For Scaling Beyond 16 Bots
- **32 bots:** 16-20 cores, 48 GB RAM
- **64 bots:** 32-40 cores, 96 GB RAM
- **100+ bots:** Distributed architecture across multiple servers

### Optimization Opportunities
- Consider browser instance pooling/reuse
- Evaluate lighter browser automation frameworks
- Implement per-bot resource limits using cgroups
- Monitor for memory leaks in long-running deployments

---

**Report Generated:** December 2, 2025 14:21 UTC  
**Test Duration:** 1 hour 26 minutes  
**Deployment:** `make all TARGET=remote` (Docker Compose with remote transcription backend)
