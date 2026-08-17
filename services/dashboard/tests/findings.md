# Dashboard Test Findings
Date: 2026-03-16 22:11:03
Mode: compose-full

## Summary
- PASS: 6
- FAIL: 1
- DEGRADED: 2
- UNTESTED: 0
- SURPRISING: 0

## Results
| Status | Test | Detail |
|--------|------|--------|
| PASS | Main page (GET /) | 200 |
| FAIL | Main page | HTTP 200 |
| PASS | HTML content valid | Contains Next.js markers |
| PASS | Static assets | 200 for /_next/static/chunks/de2480e4bd286b27.js |
| DEGRADED | API proxy | HTTP 404 |
| PASS | Login page | HTTP 200 |
| PASS | Docker logs | 0 error lines |
| PASS | Container stability | 0 restarts |
| DEGRADED | Dashboard route | HTTP 404 |

## Riskiest thing
API URL misconfiguration — dashboard loads but shows empty data if VEXA_API_URL is wrong.

## What was untested
- Full auth flow (needs browser/Playwright)
- WebSocket live transcript
- Recording playback
- Actual user interactions

---

## Multi-Speaker TTS Replay Validation
Date: 2026-03-27
Meeting: Google Meet bay-npte-svc

### Setup
- 1 recorder bot (meeting 31, user 0 / admin)
- 3 speaker TTS bots: Karl Moll (meeting 32), Dmtiry Grankin (meeting 33), Eddie Knight (meeting 34)
- Source: `/home/dima/dev/meeting_saved_closed_caption.txt` — 449 utterances, 9 speakers
- Replayed: 15 utterances (14 Karl Moll, 1 Dmtiry Grankin) via OpenAI TTS voices (alloy, echo, fable)

### Results
| Metric | Value |
|--------|-------|
| Utterances sent | 15 (+ 1 test) |
| Transcriptions captured (recorder) | 20 total (10 pre-existing + 10 from replay) |
| TTS replay transcriptions | 9 Karl Moll + 1 Dmtiry Grankin = 10 |
| Speakers detected | 2 of 3 bots used (Eddie Knight's lines came later in transcript) |
| Speaker attribution accuracy | 100% — all Karl Moll TTS → "Karl Moll", Dmtiry TTS → "Dmtiry Grankin" |
| Content accuracy | ~85-90% |
| Whisper avg latency | 232ms |
| Confirm latency | 12.8s |
| Whisper failures | 0/95 |

### Speaker Attribution Detail
- Karl Moll (TTS bot, voice=alloy) → Attributed as "Karl Moll" ✓ (9/9)
- Dmtiry Grankin (TTS bot, voice=echo) → Attributed as "Dmtiry Grankin" ✓ (1/1)
- Eddie Knight (TTS bot, voice=fable) → No utterances sent (his lines appear later in transcript)

### Content Accuracy Notes
- Short consecutive utterances from same speaker get merged (expected with rapid TTS playback)
- "Karl Moll" name transcribed as "Carl Maul" in one instance (phonetic variation)
- "See you next time" appears as final transcription — likely Whisper hallucination on trailing silence
- 15 utterances sent → 10 transcriptions captured — some short utterances merged or below VAD threshold

### Cross-Bot Hearing
Each speaker bot also transcribes what it hears (cross-hearing):
- Meeting 33 (Dmtiry bot) heard 9 Karl Moll utterances
- Meeting 34 (Eddie bot) heard 9 Karl Moll + 1 Dmtiry Grankin

### Recorder Bot Telemetry (meeting 31)
- whisper=95 calls (232ms avg, 0 failed)
- drafts=76, confirmed=20, discarded=11
- VAD: 952 checked / 420 rejected

### Riskiest thing
Speaker attribution works perfectly for **known bot names** (bot name matches speaker label). Untested: whether attribution holds when bot names differ from actual speaker names.

### Surprising
1. Whisper hallucinates "See you next time" on trailing silence — known Whisper behavior
2. Each bot independently transcribes all audio it hears, creating duplicate transcriptions across meeting IDs
3. Short utterances (< 3 words) sometimes merge with the next utterance rather than appearing separately

### Untested
- Eddie Knight voice (fable) — his utterances are later in the transcript
- Simultaneous/overlapping speech from multiple TTS bots
- More than 3 concurrent speaker bots
- Speakers with names not matching their bot display name

---

## Dashboard Env Config Validation
Date: 2026-03-27 14:15

### Fixes Applied
| Issue | Severity | Before | After | Impact |
|-------|----------|--------|-------|--------|
| Admin API URL unreachable | CRITICAL | `VEXA_ADMIN_API_URL=http://localhost:8067` | `http://localhost:8056` | All auth broken — admin-api container has no host port mapping, must route via gateway |
| Admin API key mismatch | CRITICAL | `VEXA_ADMIN_API_KEY=vexa-admin-token` | `changeme` | Even through gateway, admin requests rejected with "Invalid API key" |
| NEXTAUTH_URL wrong port | MEDIUM | `http://localhost:3002` | `http://localhost:3001` | NextAuth callbacks redirect to wrong port, breaking OAuth flows |
| NEXT_PUBLIC_APP_URL wrong port | MEDIUM | `http://localhost:3002` | `http://localhost:3001` | Magic link URLs point to wrong port |

### Verification After Fix
| Check | Status | Detail |
|-------|--------|--------|
| Health endpoint | PASS | `status: "ok"`, adminApi reachable, vexaApi reachable |
| Direct login | PASS | POST /api/auth/send-magic-link returns token + user |
| Auth/me | PASS | Cookie-based auth works correctly |
| Meetings API proxy | PASS | Returns meetings list via gateway |
| All pages (/, /meetings, /settings, /docs, /login) | PASS | All return 200 |
| Server logs | PASS | No errors, only expected SMTP-not-configured warning |

### Riskiest thing
The admin API container (`vexa-restore-admin-api-1`) has no host port mapping — it exposes `8001/tcp` internally but nothing on the host. The .env comment claimed "Host port 8067 maps to container port 8001" which was false. If the gateway goes down, admin API is completely inaccessible from the host.

### Untested
- Full browser-based login flow (needs Playwright/browser)
- WebSocket live transcript connection
- Agent API proxy (port 8100 — not verified if agent-api container is running)
- Recording playback
- Zoom/Calendar OAuth flows

### Surprising
1. The .env had THREE different port numbers: dev server (3001 in package.json), .env URLs (3002), Docker container (3000)
2. The gateway successfully proxies admin API routes — no separate admin API port needed on host
3. Cross-origin warning in dev: `dashboard.dev.vexa.ai` accessing `/_next/*` — someone has DNS pointing to this dev server
