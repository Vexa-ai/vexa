# Troubleshooting: No Live Transcript

When the bot is in the meeting and status shows **active** but no transcript appears, work through the steps below in order.

---

## Quick checklist (do these first)

| Step | What to do |
|------|------------|
| 1 | **Unmute the bot** in Teams Participants (click bot → Unmute). |
| 2 | **Speak clearly** after the bot shows "active" (transcript can lag 5–15 seconds). |
| 3 | **Set AWS credentials** for the transcription-gateway (see [§ 5](#5-aws-credentials-for-transcription-gateway)). |
| 4 | **Confirm token secret** matches: `ADMIN_API_TOKEN` (or `ADMIN_TOKEN`) must be the same in bot-manager and transcription-collector. |
| 5 | **Watch logs** with `make logs-transcript` while in a meeting to see where the pipeline stops. |

---

## 1. Unmute the bot in Teams

In the **Participants** panel, the bot may show as **(Unverified)** with its **microphone muted**. Some Teams policies only send remote audio to participants that can send audio.

- Click the bot (e.g. **VexaFirstTestBot**), then **Unmute** (or use the mic icon).
- Keep the bot unmuted while you speak so it can receive and send your audio to the transcription gateway.

## 2. Speak after the bot has joined

Live transcript only appears when there is **speech** in the meeting. After the bot joins and status is **active**, speak clearly; transcript segments may take a few seconds to appear.

## 3. Check transcription pipeline logs

From the repo root, run:

```bash
make logs-transcript
```

Watch for:

- **transcription-gateway**: `Transcription gateway ... listening on ws://...`, WebSocket connections, and any `ERROR` or `AWS Transcribe` messages. If you see `AWS Transcribe not available` or credential errors, set `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or use an IAM role) and ensure `AWS_REGION` is set.
- **transcription-collector**: `Published ... changed segments to tc:meeting:...` — that means segments are being published to the WebSocket channel. If you never see this, the gateway may not be receiving audio or AWS may not be returning results.
- **api-gateway**: WebSocket subscribe/unsubscribe and any errors.

## 4. Check bot container logs (Docker)

If the bot runs in Docker, inspect its logs to see if it is receiving remote audio and talking to the gateway:

```bash
docker ps -a   # find the vexa-bot container (often Exited if it crashed)
docker logs <container_id> --tail 200
```

Look for:

- `[Node.js] Using transcriber URL for Teams: ws://transcription-gateway:9090` — transcriber URL is set.
- `Teams Server is ready.` — gateway accepted the connection and sent SERVER_READY.
- `[Audio Hook] Injected remote audio element` — the bot is receiving remote audio tracks from Teams.
- `No active media elements found` or `Failed to create combined audio stream` — the bot is not getting remote audio (often when the bot is muted or Teams does not send audio to unverified participants).
- `Failed to send Teams audio data to transcriber` — WebSocket send failed.

## 5. AWS credentials for transcription-gateway

The gateway uses **AWS Transcribe Streaming** only. Without valid AWS credentials it will not produce any segments.

- In `.env` (or the environment for `transcription-gateway`), set:
  - `AWS_REGION` (e.g. `us-east-1`)
  - `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (or rely on IAM role if the process runs in AWS)
- Restart the gateway after changing env:  
  `docker compose up -d transcription-gateway`
- To confirm the gateway is using AWS, set `LOG_LEVEL=DEBUG` for `transcription-gateway` and watch for "Config received" when the bot connects and any "AWS Transcribe" or "ERROR" lines.

### How to check whether AWS Transcribe is working

1. **Run the AWS check script** (uses the same env as the gateway, including `.env`):
   ```bash
   make check-aws-transcribe
   ```
   This verifies `AWS_REGION`, credentials (via AWS STS), and that the Transcribe Streaming API is reachable. If any step fails, fix that before expecting live transcripts.

2. **During a meeting**: With `make logs-transcript` running, when you speak you should eventually see in **transcription-gateway** logs:
   - `Config received: uid=..., meeting_id=..., platform=...` when the bot connects.
   - `Pushed N segment(s) to Redis (AWS Transcribe returned text)` when AWS returns speech. If you never see this line, either the bot is not sending audio, or AWS is not returning results (credentials, region, or no speech detected).

## 6. Token secret (collector must verify bot’s token)

The transcription-collector verifies the JWT token the bot sends. It uses `ADMIN_TOKEN` or `ADMIN_API_TOKEN` as the signing secret.

- **Same value everywhere**: The value must be **identical** in:
  - **bot-manager** (used to mint the token)
  - **transcription-collector** (used to verify the token)
- In `docker-compose.yml`, both get `ADMIN_TOKEN=${ADMIN_API_TOKEN}`. So set `ADMIN_API_TOKEN` in `.env` once and leave it the same; do not use different tokens for different services.
- If the secret does not match, the collector will log something like "MeetingToken verification failed" and will not process segments for that meeting.

## 7. Environment and connectivity

- **TRANSCRIBER_WS_URL**: Bot-manager and the bot container must use the same URL (e.g. `ws://transcription-gateway:9090`). Default in compose is correct when the bot runs on the same Docker network.
- **Redis**: Gateway and transcription-collector must use the same Redis and stream name (`transcription_segments`). Defaults in docker-compose are correct.

## 8. How to see where it breaks (logs)

Run in one terminal while you join a meeting and speak:

```bash
make logs-transcript
```

Interpret what you see:

| Log source | What you want to see | If you don’t see it |
|------------|----------------------|----------------------|
| **transcription-gateway** | "Transcription gateway ... listening", then when the bot connects either "Config received" (if LOG_LEVEL=DEBUG) or no `_path`/handler errors | Bot may not be connecting, or URL/network wrong. Check bot logs for transcriber URL and connection errors. |
| **transcription-gateway** | No "AWS Transcribe not available" or "ERROR" / credential errors | Set AWS credentials and restart gateway. |
| **transcription-collector** | "Stored/Updated ... segments" and "Published ... changed segments to tc:meeting:..." | Gateway may not be pushing (check AWS and gateway logs), or token verification failing (check ADMIN_TOKEN / ADMIN_API_TOKEN). |
| **Bot container** | "Using transcriber URL ...", "Teams Server is ready", "Injected remote audio element" | Bot not getting transcriber URL, or not receiving remote audio (unmute bot, check Teams). |

To get more detail from the gateway, set in docker-compose for `transcription-gateway`:

```yaml
environment:
  - LOG_LEVEL=DEBUG
```

Then rebuild/restart and run `make logs-transcript` again.
