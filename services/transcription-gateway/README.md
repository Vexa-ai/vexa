# Transcription Gateway (AWS Transcribe)

WebSocket server that accepts audio from the Vexa bot and transcribes it using **Amazon Transcribe Streaming**. Pushes segments to the same Redis stream consumed by the transcription-collector, so no changes are required downstream.

## Flow

1. Bot connects to `ws://transcription-gateway:9090` (or `TRANSCRIBER_WS_URL`).
2. Bot sends initial JSON config: `uid`, `token`, `platform`, `meeting_id`, `language`, etc.
3. Gateway sends `session_start` to Redis, then starts an AWS Transcribe Streaming session.
4. Gateway responds with `SERVER_READY` so the bot starts sending audio.
5. Bot sends binary Float32 audio (16 kHz); gateway converts to PCM 16-bit and streams to AWS.
6. AWS returns partial/final transcripts; gateway pushes them to Redis stream `transcription_segments` in the same payload format the collector expects.
7. Speaker and session_control messages from the bot are forwarded to Redis (speaker_events stream / session_end).

## Environment

| Variable | Description |
|----------|-------------|
| `TRANSCRIPTION_GATEWAY_WS_HOST` | Bind host (default `0.0.0.0`) |
| `TRANSCRIPTION_GATEWAY_WS_PORT` | Bind port (default `9090`) |
| `REDIS_URL` | Redis connection URL |
| `REDIS_STREAM_NAME` | Stream for transcription segments (default `transcription_segments`) |
| `AWS_REGION` | AWS region for Transcribe (e.g. `us-east-1`) |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | AWS credentials (or use IAM role) |
| `DEFAULT_LANGUAGE` | Default language code if client omits it (e.g. `en-US`) |

## AWS Permissions

The service needs permission to call `transcribe:StartStreamTranscription`. Example IAM policy:

```json
{
  "Effect": "Allow",
  "Action": "transcribe:StartStreamTranscription",
  "Resource": "*"
}
```

## Local run

```bash
pip install -r requirements.txt
export AWS_REGION=us-east-1
export REDIS_URL=redis://localhost:6379/0
python -m main
```

Then set `TRANSCRIBER_WS_URL=ws://localhost:9090` for the bot.
