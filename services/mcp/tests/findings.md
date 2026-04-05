# MCP Service Test Findings
Date: 2026-03-16 22:11:02
Mode: compose-full

## Summary
- PASS: 6
- FAIL: 0
- DEGRADED: 0
- UNTESTED: 0
- SURPRISING: 1

## Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | MCP port reachable | HTTP 404 |
| PASS | MCP OpenAPI docs | 200 |
| PASS | MCP via gateway | HTTP 200 |
| PASS | MCP SSE endpoint | HTTP 406 |
| PASS | Docker logs | 0 error lines |
| PASS | Container stability | 0 restarts |
| SURPRISING | MCP /health | 404 — no health endpoint |

## Riskiest thing
MCP is a stateless proxy — if gateway is down, all MCP tools fail silently.

## What was untested
- Actual MCP tool invocation (needs MCP client/SDK)
- Auth token forwarding through MCP to gateway
- All individual tools (start_bot, get_transcript, etc.)
