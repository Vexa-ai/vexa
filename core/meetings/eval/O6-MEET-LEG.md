# O6 Meet-leg — one-sequence validation of the carved v0.12 bot

Validates the **actual** `vexaai/vexa-bot:v012` end-to-end on a live Google Meet, on the controllable
**bbb** host, scored against [`BASELINE.md`](BASELINE.md). Everything here is proven EXCEPT the
audio→STT step, which only runs once real audible speech is in the meeting (the speaker-bots must be
driven — that needs valid prod speaker tokens). The bot side (join · admit · capture pipeline ·
redis transcript egress · scoring) is all proven live (RELEASE-PLAN O6 · Learning #23).

## Prereqs
- `ssh bbb` works; `vexaai/vexa-bot:v012` present on bbb (built from `/home/dima/v012-bot-build`).
- The speaker-bots are **already admitted** in the target meeting `$NATIVE` (e.g. `rvf-kywf-pxb`).
- Your **prod speaker secrets** sourced locally so the eval rig can drive them:
  ```bash
  export VEXA_BASE=https://api.cloud.vexa.ai PLATFORM=google_meet NATIVE_ID=rvf-kywf-pxb
  export TOK_A=<anna-token> TOK_G=<zoya-token> TOK_C=<galina-token>   # one TOK_<key> per speaker
  ```
  (The throwaway/test-users tokens are `Invalid API key` on prod — these must be the tokens that own
  the bots in the meeting.)

## 1 — spawn the carved bot into the meeting (on bbb, STT from the running stack)
```bash
SINCE=$(( $(date +%s) * 1000 ))   # only read segments produced from here on
ssh bbb '
  TT=$(docker exec vexa-runtime-api-1 printenv TRANSCRIPTION_SERVICE_TOKEN)
  TU=$(docker exec vexa-runtime-api-1 printenv TRANSCRIPTION_SERVICE_URL)
  CFG=$(TT="$TT" TU="$TU" N="'"$NATIVE_ID"'" python3 -c "import json,os;print(json.dumps({\"platform\":\"google_meet\",\"meetingUrl\":\"https://meet.google.com/\"+os.environ[\"N\"],\"botName\":\"vexa-0.12-bot\",\"nativeMeetingId\":os.environ[\"N\"],\"connectionId\":\"o6-meet-leg\",\"redisUrl\":\"redis://redis:6379\",\"transcriptionServiceUrl\":os.environ[\"TU\"],\"transcriptionServiceToken\":os.environ[\"TT\"],\"recordingEnabled\":True,\"automaticLeave\":{\"waitingRoomTimeout\":300000,\"everyoneLeftTimeout\":900000}}))")
  docker rm -f v012-meet >/dev/null 2>&1
  docker run -d --network vexa_vexa --name v012-meet -e VEXA_BOT_CONFIG="$CFG" vexaai/vexa-bot:v012
'
```

## 2 — admit it (the human step)
Admit **`vexa-0.12-bot`** in the meeting. Confirm it reached `active`:
```bash
ssh bbb "docker logs v012-meet 2>&1 | grep -E 'lifecycle.v1 (active|failed)|capture started|ctx.state'"
```

## 3 — drive your speaker-bots (named audio)
```bash
node src/drive.mjs   # speaks rotating named clips into the meeting; Ctrl-C after ~2 min
```

## 4 — read the bot's transcript.v1 from bbb redis + score
The carved bot publishes to the `transcription_segments` stream (no gateway meeting-record on a
standalone run), so read redis directly and score with the file source:
```bash
ssh bbb "docker exec vexa-redis-1 redis-cli XRANGE transcription_segments $SINCE +" \
  | node src/read-redis-transcript.mjs > /tmp/o6-transcript.json
TRANSCRIPT_FILE=/tmp/o6-transcript.json node src/analyze.mjs google_meet "$NATIVE_ID"
```
Pass if the `SCORE` line meets `BASELINE.md` gmeet row (`misattr=0`, low oversegmentation). Check the
recording master: `ssh bbb 'docker exec v012-meet ls -la /app/recordings 2>/dev/null'` (or the
`recordingUploadUrl` target).

> The read→score instrument (`read-redis-transcript.mjs` + `analyze.mjs TRANSCRIPT_FILE`) is unit-proven
> on a synthetic dump — it extracts the carved bot's transcript.v1 entries (skips legacy token/`segments[]`
> format) and flags content-vs-label mis-attribution. Only step 3's audio→STT is unproven pre-run.
