# TTS Service Test Findings
Date: 2026-03-16 22:11:03
Mode: compose-full

## Summary
- PASS: 4
- FAIL: 1
- DEGRADED: 2
- UNTESTED: 0
- SURPRISING: 0

## Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Container running | running |
| FAIL | Container | running |
| PASS | Container stability | 0 restarts |
| DEGRADED | Health | HTTP OCI runtime exec failed: exec failed: unable to start container process: exec: "curl": executable file not found in $PATH |
| PASS | Docker logs | 0 error lines |
| DEGRADED | OPENAI_API_KEY | Not set — synthesis will return 503 |
| PASS | Startup logs | Service started normally |

## Riskiest thing
No OPENAI_API_KEY means all voice agent speak commands fail silently.

## What was untested
- Actual speech synthesis (requires OPENAI_API_KEY with TTS access)
- Audio format variations
- Voice selection fallback
- TTS_API_TOKEN auth enforcement
