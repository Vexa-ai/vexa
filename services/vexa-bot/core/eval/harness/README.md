# Live ingest diarization harness

Streams a mixed mono audio file through the REAL ingest WebSocket (exactly as
the extension would) to verify the single-channel diarization pipeline E2E
without a live meeting, a browser, or a tabCapture gesture.

Prep (16 kHz mono f32le):
  ffmpeg -y -i <multispeaker.wav> -t 100 -ac 1 -ar 16000 -f f32le /tmp/diartest.f32

Run (TOK = a valid local api key):
  node diar-stream.cjs        <TOK>   # mixed track only → clusters stay speaker_N
  node diar-stream-hints.cjs  <TOK>   # + speaker_activity hints → clusters rename

Verify:
  docker logs vexa-ingest-server-1 | grep Diarize   # new cluster / late-resolve
  psql … "SELECT speaker, count(*) FROM transcriptions WHERE meeting_id=<id> GROUP BY 1"

Verified 2026-06-11: All-In clip (100s) → 2 distinct clusters with correct
transcripts; hints drove late-resolve + window-match renames. Zero errors.
