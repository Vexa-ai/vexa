# Recall Backup Service

Automatic fallback to Recall.ai when Vexa bot fails to join a meeting.
Audio from Recall bot is piped to WhisperLive for transcription — same Vexa experience, different audio source.

## Architecture

```
Customer → POST /bots → Bot Manager
                           │
                    ┌───────┴───────┐
                    ▼               ▼ (on failure)
               Vexa Bot        Recall Backup
               (Playwright)    (Recall.ai API)
                    │               │
                    │         audio_mixed_raw
                    │          via WS push
                    │               │
                    ▼               ▼
                  Redis ◄──── Recall Adapter
                  Streams      (audio → Redis)
                    │
                    ▼
               WhisperLive
                    │
                    ▼
            Transcription Collector
                    │
                    ▼
              Customer (via /ws or webhook)
```

## Status: Scaffold — not yet implemented
