# Infrastructure

## Why

Everything depends on the stack running. If services aren't healthy, nothing else works.

## What

```
make build → immutable tagged images
make up → compose stack running
make test → all services respond
```

### Components

| Component | Path | Role |
|-----------|------|------|
| Compose stack | `deploy/compose/` | Docker Compose, Makefile, env |
| Helm charts | `deploy/helm/` | Kubernetes deployment |
| Env config | `deploy/env/` | env-example, defaults |
| Deploy scripts | `deploy/scripts/` | Fresh setup automation |

## DoD

| # | Check | Weight | Ceiling | Floor | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|-------|--------|----------|--------------|-------|
| 1 | make build produces immutable tagged images | 20 | ceiling | 0 | SKIP | Build step not part of this test session (pre-built images used) | 2026-04-05T19:40Z | 01-infra-up |
| 2 | make up starts all services healthy | 25 | ceiling | 0 | PASS | 14/14 services healthy, all endpoints responding (compose mode) | 2026-04-05T19:40Z | 01-infra-up |
| 3 | Gateway, admin, dashboard respond | 20 | ceiling | 0 | PASS | gateway → 200, admin /users → 200, dashboard serving on :3001 | 2026-04-05T19:40Z | 01-infra-up, 02-api, 04-dashboard |
| 4 | Transcription service has GPU | 15 | — | 0 | PASS | transcription-lb: gpu_available=True | 2026-04-05T19:40Z | 01-infra-up, 02-api |
| 5 | Database migrated and accessible | 10 | — | 0 | PASS | meetings list → 200, bots list → 200, users → 200 | 2026-04-05T19:40Z | 02-api |
| 6 | MinIO bucket exists | 10 | — | 0 | PASS | Recordings uploaded and retrieved for both platforms | 2026-04-05T19:40Z | 10-verify-post-meeting |

Confidence: 80 (item 1 SKIP; items 2+3+4+5+6 pass = 80/100)
