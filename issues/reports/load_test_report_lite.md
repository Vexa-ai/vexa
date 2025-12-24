# Bot Resource Scaling Formulas

**Based on load testing:** December 18, 2025

**Test Configuration:** This load testing was performed using the [`docker/lite`](../docker/lite/) deployment configuration. The lite deployment runs all services (API Gateway, Bot Manager, transcription services, and bot processes) within a single container, with bots spawned as Node.js child processes rather than separate Docker containers. See the [`docker/lite/README.md`](../docker/lite/README.md) for deployment details.

## Observed Scaling Data

| Bots | Total RAM | Chrome RAM | CPU % | Load Avg |
|------|-----------|------------|-------|----------|
| 4 | 8.0 GB | 1.0 GB | 39% | 2.11 |
| 8 | 9.4 GB | 1.8 GB | 59% | 8.91 |
| 12 | 12.4 GB | 2.5 GB | 87% | 21.99 |

## Resource Requirements Formulas

### Memory Formula

**Base System:** 7.0 GB  
**Per Bot:** 0.35 GB

```
RAM (GB) = 7.0 + (0.35 × bot_count)
```

**With 20% safety buffer:**
```
Recommended RAM (GB) = [7.0 + (0.35 × bot_count)] × 1.2
```

### CPU Formula

**Per Bot:** 10% of one CPU core (minimum)  
**Per Bot:** 15% of one CPU core (recommended)

```
CPU Cores (minimum) = bot_count × 0.10
CPU Cores (recommended) = bot_count × 0.15
```

**Note:** Load average grows non-linearly due to system overhead (~2x per bot doubling).

### Swap Formula

**Minimum:** 30% of RAM (prevents swap exhaustion)  
**Recommended:** 50% of RAM (comfortable buffer)

```
Swap (GB) = RAM × 0.5
```

### Chrome Memory Scaling

**Per Bot Instance:** ~0.25 GB

```
Chrome RAM (GB) = bot_count × 0.25
```

## Quick Reference: Resource Planning

| Bots | RAM (GB) | CPU Cores | Swap (GB) |
|------|----------|-----------|-----------|
| 4 | 10 | 1 | 5 |
| 8 | 12 | 2 | 6 |
| 12 | 16 | 2-3 | 8 |
| 16 | 18 | 3 | 9 |
| 20 | 21 | 3-4 | 11 |

*Values include 20% safety buffer for RAM, recommended CPU cores, and 50% swap.*

## Scaling Characteristics

1. **Memory scales linearly:** +0.35 GB per bot (base 7 GB)
2. **CPU scales linearly:** ~15% of one core per bot
3. **Chrome memory scales linearly:** ~0.25 GB per bot instance
4. **Load average grows non-linearly:** System overhead compounds with more bots

## Formula Summary

```
RAM (GB) = [7.0 + (0.35 × bots)] × 1.2
CPU Cores = bots × 0.15
Swap (GB) = RAM × 0.5
```
