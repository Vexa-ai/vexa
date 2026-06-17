# capture.v1 — meeting capture stream (to formalize at MVP2)

One format, three jobs: the bot/extension → pipeline wire format, the recorder's
output, and the pipeline test input.

Shape (MANIFEST §2): `events.jsonl` (one timestamped JSON object per line —
join/leave, active-speaker, captions, chat, admission state; capture-kit speaker
hints ride here) + `audio/` chunks (PCM or Opus; start timestamp, duration,
**channel id**) + `meta.json` declaring topology: `channels: per-participant`
(identity free by channel) or `channels: mixed` (diarization required downstream).

Embryos to formalize from (do not invent — recover):
- `services/vexa-bot/core/src/ingest-server.ts` — documented WS frames:
  `{type:'speakers',...}` text frame + `[Int32 speakerIndex][Float32 pcm]` binary.
- `services/vexa-bot/core/src/services/raw-capture.ts` — per-speaker WAVs +
  timestamped `events.txt` dumps (`RAW_CAPTURE=true`).
