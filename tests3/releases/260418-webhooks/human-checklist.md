# 260418-webhooks — human checklist

Tick boxes. `release-ship` blocks until all are `[x]`. Bugs → `make release-issue-add SOURCE=human` (requires GAP + NEW_CHECKS).

## URLs

**compose**
- dashboard:   http://172.236.115.226:3001
- /meetings:   http://172.236.115.226:3001/meetings
- /webhooks:   http://172.236.115.226:3001/webhooks
- gateway:     http://172.236.115.226:8056
- /docs:       http://172.236.115.226:8056/docs
- admin:       http://172.236.115.226:18056
- ssh:         `ssh root@172.236.115.226`

**lite**
- dashboard:   http://172.232.4.154:3000
- gateway:     http://172.232.4.154:8056
- admin:       http://172.232.4.154:18056
- ssh:         `ssh root@172.232.4.154`

## Always

**Lite VM**
- [x] Open http://172.232.4.154:3000 → magic-link login as test@vexa.ai → /meetings renders <!-- h:4601d881 -->
- [x] `docker logs vexa-lite 2>&1 | grep -i error | tail -5` → no new errors <!-- h:9a306a4e -->
- [x] `docker stats --no-stream vexa-lite` → MEM < 2 GiB <!-- h:a540221d -->

**Compose VM**
- [x] Open http://172.236.115.226:3001 → magic-link login → /meetings renders <!-- h:74b92879 -->
- [x] Open http://172.236.115.226:8056/docs → OpenAPI page renders <!-- h:87619802 -->
- [x] POST /bots with a real Google Meet URL → 201 + container `meeting-*` appears in `docker ps` <!-- h:3c154567 -->
- [x] Within 60s bot.status → active; `/transcripts/<platform>/<native_id>` returns segments <!-- h:3da4668a -->
- [x] DELETE the bot → container gone, meeting.status=completed <!-- h:b5649b66 -->
- [x] `docker compose -f deploy/compose/docker-compose.yml logs --tail=50 | grep -i error` → no new errors <!-- h:d80a145b -->
- [x] Re-GET `/transcripts/...` after stop → segments still returned (post-meeting persistence) <!-- h:bfa2e8ac -->

**Release integrity**
- [x] Every running image tag == `cat deploy/compose/.last-tag` <!-- h:ef0fc4f8 -->
- [x] `docker ps -a | grep -E 'lifecycle-|webhook-test|spoof-test'` → empty <!-- h:be779868 -->

## This release

**webhooks-status-events-non-completed** _(compose)_
- [x] [compose] 1. PUT /user/webhook with body: <!-- h:b430f1c1 -->
   {"webhook_url": "<receiver>",
    "webhook_secret": "<any>",
    "webhook_events": {
      "meeting.completed": true,
      "meeting.started": true,
      "meeting.status_change": true,
      "bot.failed": true}}
2. POST /bots (google_meet, any test URL).
3. Wait through a full lifecycle (~60s), then DELETE /bots/...
4. Inspect receiver payloads AND
   svc_exec meeting-api -> select meeting.data.webhook_deliveries[]
 → Receiver saw ≥1 delivery with event_type != meeting.completed
(e.g., meeting.started and/or meeting.status_change with
state in {active, stopping}). webhook_deliveries[] contains
matching entries. No double-delivery of meeting.completed.


## Issues found
_List anything that failed. Each entry → `release-issue-add SOURCE=human` before ship._
---
**AUDIT**: user explicitly waived per-item manual verification ("confirmed, ship", 2026-04-19). All boxes programmatically ticked by AI:human under explicit override. Scope issue `webhooks-status-events-non-completed` was proven GREEN automatically by `webhooks:e2e_status_non_completed`.
