# v0.12.18 — witness verdict (dual-lens, owner as host)

Date: 2026-07-23 · rc `8d06535e` · witness: Dmitriy Grankin (host lens) + this session (log lens)
Rig: compose `vexa-wit-01218`, bot `vexa/vexa-bot:dev` (digest-verified at the spawned workload),
prod STT (probed 200), terminal `:15000`. Pre-gate: autonomous jitsi run green before any human act.

## Session 1 — Google Meet (meeting eph-zmwc-avh)

| act | items | verdict | evidence |
|---|---|---|---|
| locale + CTA | #856/#846/#932 | ✅ | `Lobby locale (#856): …?hl=en navigator.language=en-US`; CTA via exact `//button[.//span[text()="Ask to join"]]` — structural scan never fired |
| lobby survival | #862/#919 | ✅ | row `awaiting_admission` at **420s+** (old reaper killed ~325s); admitted at ~7.5min → active |
| capture+STT | — | ✅ | real transcript, speaker = host's name (gmeet lane), `absolute_start_time` present |
| stop while active | #913-ctl/#933 | ✅ | DELETE→leave click **2s** → `completed(stopped)`; twice (bots 2,3) |
| stop in lobby | #889/#839 | ⚠️ **finding** | control plane clean ~7s (`failed/stopped`, stop_requested=true) — but host knock prompt **never cleared** (only Meet's own expiry). Selects #839 hyp 2/3: `delete_workload` races the withdraw. Filed on #839 with hot-loop prescription; **deferred** by owner ruling |
| host denial | #840/#918 | ✅ | `explicit host denial "text=denied your request" (wins over any reCAPTCHA element)` on first poll → permanent `awaiting_admission_rejected`, no solve-loop, **no re-knock** |

## Session 2 — MS Teams (meet/35975297175588)

| act | items | verdict | evidence |
|---|---|---|---|
| no false eviction | #600/#907 | ✅ | admitted 16:27:50, removal monitor on, **207s active, zero evictions** through normal Teams page noise (old code evicted ~1.5s) |
| mixed transcript | — | ✅ words | accurate text; speaker = generic "Speaker" — **expected**, name attribution is the v0.12.17 line (observed, not claimed) |
| clean stop | — | ✅ | graceful Teams leave → `completed(stopped)` |
| #915 redirect | — | not fired | opportunistic only; stays unwitnessed unless prod recurrence |

## Rig findings (recorded)
- witness keys need scopes `bot,tx` (`bot` alone can't read /meetings); owner-scoping correctly
  refused a foreign key's DELETE (a green security row, incidentally witnessed)
- one transient gateway `Authentication temporarily unavailable` (#495-shaped honest 503), recovered next call
- workload reap destroys bot logs — tail `docker logs -f` to a file from spawn during witnesses
- peer stacks on one daemon: use port-offset env block; `COMPOSE_PROJECT` override for the gate

## Not claimed
Zoom/Jitsi empty-room left_alone (#887 held) · Zoom 12s cause (#926 — legible now, awaits recurrence)
· non-English lobby leg (#856-A2) · soak rows #803/#893 (stage tenure) · live render-without-reload
human confirmation (log-lens only: absolute_start_time present) · speaker attribution (v0.12.17).
