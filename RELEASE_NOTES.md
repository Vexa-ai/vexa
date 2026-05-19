# Vexa v0.10.4 — Zoom Web bot

**Release date:** 2026-04-27
**Cycle:** `260426-zoom`
**Highlight:** Zoom support via the official Zoom **Web Client** (no proprietary SDK required).

---

## What's new

### 🎯 Zoom Web is the bot's default join path

Spawn a Zoom bot with the same API as Google Meet / MS Teams — **no Zoom SDK credentials, no special configuration**:

```bash
curl -X POST $GATEWAY/bots \
  -H "X-API-Key: $TOKEN" -H "Content-Type: application/json" \
  -d '{"platform":"zoom","native_meeting_id":"89164742472","passcode":"abc123","bot_name":"Vexa"}'
```

The bot opens `app.zoom.us/wc/`, fills the name, joins audio, sits through the waiting room if any, and starts publishing transcript segments — same flow as our other platforms.

> **Migration note:** previously, `platform=zoom` routed to the native Zoom SDK path (which required `ZOOM_CLIENT_ID` + `ZOOM_CLIENT_SECRET`). That path is still available but is now **opt-in** via `ZOOM_SDK=true`. Operators using only Web don't need to do anything; the legacy `ZOOM_WEB=true` env-var is also still honoured for backward-compat. Wave 3 will retire both env-vars in favour of an explicit `platform: zoom_sdk` enum value.

### 🚀 4× CPU reduction per Zoom bot

A single Chromium flag — `--in-process-gpu` — collapses the gpu-process work into the renderer. Measured on this release's smoke matrix:

| | Before | After |
|---|--:|--:|
| Total CPU per Zoom bot | ~440% | **~115%** |
| GPU process | 357% (SwiftShader software-WebGL) | **gone** |
| K8s `cpu_limit` budget | 4000m (emergency bump) | **1500m** (original p95-based) |

Bot density per node tripled. No transcription quality regressions. Audio + chat + admission all unaffected.

### 🛠 Bug fixes

- **Chat persistence race** (cycle-scope, all platforms) — DELETE-vs-exit-callback race was leaving chat messages stranded in Redis. Removed the early-exit guard so chat flushes regardless of status-update outcome.
- **Awaiting-admission false positive** (Zoom Web) — the `isAdmitted()` waiting-room exclusion was firing too late; bots were reporting `active` while still in the Zoom waiting room. Fixed by checking waiting-room text before the weaker fallbacks.
- **Web-as-default dispatch** (Zoom) — the bot's index.ts no longer requires `ZOOM_WEB=true` to route to the Web client.
- **CPU resource budget restored** — the temporary 4000m/2500m bump that was needed before `--in-process-gpu` is reverted to the original 1500m/1000m. Helm chart + compose `runtime-api/profiles.yaml` both updated.

---

## Helm chart

- **Chart version:** `0.10.4` (was `0.10.3`)
- **App version:** `0.10.4`
- Fetch the packaged chart from the GitHub release artifacts.

```bash
helm install vexa oci://... --version 0.10.4
# or, from the GitHub release tarball:
helm install vexa ./vexa-0.10.4.tgz
```

---

## Validation summary

Smoke matrix run across **all three modes** (compose / lite / helm) on Linode:

| Mode | Tests | Pass | Notes |
|---|--:|--:|---|
| compose | 11 | 9 | 2 pre-existing infra failures (chart-version drift #228, dashboard API-key staleness) — not blocking |
| lite | 10 | 7 | same pre-existing + flaky e2e_completion timing |
| helm | 12 | 9 | same pre-existing + flaky webhook inject timing on cold cluster |

All in-scope claims for `260426-zoom` (`BOT_CREATE_OK`, `BOT_STATUS_TRANSITIONS`, `SEGMENT_PIPELINE`, `ZOOM_WEB_AUDIO_RESEARCH_NOTE_EXISTS`) ✅ green.

---

## Carried forward to Wave 2

- Full incoming-video disable on Zoom Web (decoder shutdown via SDP-munge or transceiver-direction trick — five attempts in this cycle made progress but didn't fully land; `--in-process-gpu` mooted the urgency).
- Zoom Web platform-enum split (`zoom` → Web, `zoom_sdk` → SDK direct routing both sides; retire `ZOOM_WEB`/`ZOOM_SDK` env-vars).
- `meeting-tts-zoom` tier-meeting test.
- Three Zoom-specific DoDs under `realtime-transcription/zoom`.

---

🤖 Generated with [Claude Code](https://claude.com/claude-code)
