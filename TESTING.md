# Testing and Audit

This repository includes a comprehensive testing and audit kit. Anyone self-hosting Vexa can:

1. **Verify their deployment works** -- staged tests from unit to full stack
2. **Stress their infrastructure** -- find bottlenecks before users do
3. **Audit their configuration** -- catch security issues and misconfigurations
4. **Validate architecture compliance** -- enforce design principles

## Quick start

```bash
# Unit tests only (< 30s, no Docker)
make test-unit

# Unit + integration + smoke (< 10min, Docker required)
make test

# Full suite including load tests (< 30min)
make test-full

# Security, config, and architecture audit (< 2min)
make audit
```

## Test layers

### Layer 0: Unit tests

Fast, no Docker required. Tests individual service logic in isolation.

```bash
make test-unit
# or
bash tests/run_unit.sh
```

Each service has tests in `services/<name>/tests/`. Run a single service:

```bash
cd services/transcription-service && pytest tests/
```

### Layer 1: Integration tests

Cross-service tests with real infrastructure (Postgres, Redis).

```bash
docker compose up -d postgres redis
pytest tests/integration/
```

### Layer 2: Load tests

Measure latency, throughput, and resource usage under load.

```bash
# Single request baseline
python tests/load/transcription_service.py --mode single

# Concurrent throughput
python tests/load/transcription_service.py --mode concurrent --vus 10

# Memory leak detection
python tests/load/transcription_service.py --mode memory --vus 2 --duration 300

# Run all load tests
bash tests/load/run_load_test.sh
```

Results are saved to `tests/load/results/` as JSON.

### Layer 3: Smoke tests

Verify full deployment is healthy.

```bash
docker compose up -d
bash tests/smoke/test_full_stack.sh
```

### Layer 4: Agent-verified tests

Each service and test directory includes an `AGENT_TEST.md` file with instructions for agent-driven testing. These tests handle qualitative verification that is difficult to automate -- evaluating transcription quality, reviewing UI layout, analyzing security flows.

```bash
# Find all agent test files
find . -name "AGENT_TEST.md"
```

## Full test cycle

For thorough pre-release validation, use the bottom-up cycle in [tests/CYCLE.md](tests/CYCLE.md). The cycle covers 8 phases:

1. **Code quality** -- per-service unit tests, README validation (no Docker)
2. **Service isolation** -- start each service in Docker, verify it works
3. **Functionality chains** -- transcription chain, webhook delivery, API chain, real-time delivery, speaker identification
4. **User experience flows** -- self-hoster deploy, API user flow, dashboard flow
5. **Stress and load** -- transcription-service capacity, WhisperLive streams (if used), bot scaling
6. **Builds and packages** -- Docker images, Vexa-lite, Helm charts, version consistency
7. **Audit** -- security, config, architecture, staleness
8. **Report to human** -- per-phase summary, human decides next steps

The cycle is the most thorough option. `make test-unit` is fine for quick checks. `make test-all` is fine for CI.

## Audit kit

### Security audit

Scans for hardcoded secrets, default credentials, CORS issues, missing auth.

```bash
python tests/audit/security_audit.py
```

### Configuration audit

Checks env vars, NEXT_PUBLIC_ exposure, Docker ports, development defaults.

```bash
python tests/audit/config_audit.py
```

### Architecture compliance audit

Enforces design principles: no billing tables, stateless services, token scoping, durable delivery, self-hostable.

```bash
python tests/audit/architecture_audit.py
```

### Deep audit (agent-driven)

See `tests/audit/AGENT_TEST.md` for thorough agent-driven security and architecture review instructions.

## Test structure

```
services/
  <service>/tests/
    test_*.py           # deterministic unit tests
    AGENT_TEST.md       # agent-driven qualitative tests
tests/
  integration/          # cross-service tests
  load/                 # performance and resource tests
  smoke/                # deployment verification
  audit/                # security and compliance
  run_unit.sh           # run all unit tests
  run_all.sh            # run unit + integration + smoke
  CYCLE.md              # bottom-up sequential validation (8 phases)
```

## Resource baselines

Known performance baselines (from prior testing):

| Metric | Value |
|--------|-------|
| Single transcription latency | ~14.4s average |
| Bot resource usage | ~250m CPU, ~597Mi RAM |
| Concurrent degradation | 10 concurrent = ~15% coverage (known issue) |

Load test results are saved to `tests/load/results/` with timestamps for tracking improvements.
