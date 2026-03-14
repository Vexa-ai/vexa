# Tests

## Why

Vexa is a real-time audio pipeline -- bots join meetings, capture per-speaker audio, transcribe via HTTP POST to the transcription service, publish segments to Redis, persist to Postgres, and fire webhooks. Every piece can fail independently: the transcription service can degrade under load, Redis streams can back up, webhooks can timeout, bots can leak resources.

Tests exist so that self-hosters can verify their deployment works, find bottlenecks before users do, and catch security issues before they're exploited. They're not academic -- they solve real problems we've hit in production.

## What

Tests are organized in layers, cheapest first:

```
tests/
├── run_unit.sh              # Layer 0: unit tests across all services (< 30s)
├── run_all.sh               # Layers 0+1+3: unit + integration + smoke
│
├── integration/             # Layer 1: cross-service tests with real DB/Redis
│   ├── test_transcription_chain.py
│   ├── AGENT_TEST.md        # agent instructions for integration verification
│   └── results/             # persisted test outputs
│
├── load/                    # Layer 2: performance and resource tests
│   ├── transcription_service.py   # 3 modes: single, concurrent, memory
│   ├── run_load_test.sh     # orchestrator
│   ├── AGENT_TEST.md
│   └── results/             # JSON metrics, baselines, historical reports
│
├── smoke/                   # Layer 3: full stack deployment verification
│   ├── test_full_stack.sh   # service health, API, DB, Docker checks
│   ├── AGENT_TEST.md
│   └── results/
│
├── audit/                   # Code quality, security, architecture compliance
│   ├── security_audit.py    # hardcoded secrets, auth gaps, CORS, cookies
│   ├── config_audit.py      # env vars, port exposure, dev defaults
│   ├── architecture_audit.py # design principle enforcement
│   ├── staleness_audit.py   # dead code, orphaned files, stale docs
│   ├── AGENT_TEST.md
│   ├── AGENT_STALENESS_TEST.md
│   └── results/
│
├── agent/                   # Agent-generated test reports
│   └── results/
│
├── CYCLE.md                 # Bottom-up sequential validation (8 phases)
│
└── results/                 # Combined test run outputs (by date)
    └── 2026-03-14/
```

Each service also has its own `tests/` directory with unit tests and an `AGENT_TEST.md`.

## How

### Quick: verify your deployment works

```bash
make test-unit      # unit tests, no Docker needed, < 30 seconds
make test-smoke     # all services healthy? APIs respond? DB connected?
```

### Standard: full test pass before merging

```bash
make test-all       # unit + integration + smoke (< 10 min)
make audit          # security + config + architecture (< 2 min)
```

### Thorough: stress test and deep audit

```bash
make test-full      # unit + integration + smoke + load (< 30 min)
make audit          # all audit scripts
```

### Full cycle: bottom-up with human in the loop

See [CYCLE.md](CYCLE.md) -- the most thorough approach. 8 phases:

1. **Code quality** -- per-service unit tests and README validation
2. **Service isolation** -- each service starts and works in Docker
3. **Functionality chains** -- transcription chain, webhook delivery, API chain, real-time delivery, speaker identification
4. **User experience flows** -- self-hoster deploy, API user, dashboard
5. **Stress and load** -- capacity limits, baselines
6. **Builds and packages** -- Docker images, Vexa-lite, Helm charts
7. **Audit** -- security, config, architecture, staleness
8. **Report to human** -- summary and decision

The cycle is one run option -- not the only one. `make test-unit` is fine for quick checks. `make test-all` is fine for CI. The cycle is for when you want a human verifying every step.

### Single service

```bash
cd services/admin-api && pytest tests/ -v
cd services/bot-manager && pytest tests/ -v
```

### Load testing a specific service

```bash
# Single request baseline
python tests/load/transcription_service.py --mode single

# Find the breaking point
python tests/load/transcription_service.py --mode concurrent --vus 10

# Check for memory leaks
python tests/load/transcription_service.py --mode memory --vus 2 --duration 300
```

## How tests work with agents

Every test directory has an `AGENT_TEST.md` file. These are instructions that any coding agent can read and execute. The agent uses deterministic scripts as tools (start services, send requests, collect metrics) and applies its own judgment for evaluation (is this transcription quality acceptable? does the UI render correctly? is this error message actionable?).

Agents find issues and report. Humans decide what to fix. Nothing is committed without human approval.

## Results

Test outputs are persisted in `results/` directories and committed to the repo. This creates a history:
- New results are compared against baselines to detect regressions
- Agent tests read previous results to understand what "normal" looks like
- Load test baselines are tracked with dates so performance changes are visible

See `results/README.md` in each subdirectory for baseline expectations.
