### Transcription keeps up and never gives up captured audio (#1783)

- One slow transcription request no longer holds up the rest of a speaker's audio: a stalled request
  is sent again beside it, answers are used in order, and a late answer is dropped.
- Audio is uploaded as lossless FLAC (WAV when a backend refuses it), no request carries more than
  30 s, and each request's timeout follows its size.
- A window that fails is kept and retried with backoff while the meeting runs (Meet, Zoom, voice
  notes, calls and Teams); when the bot stops it retries everything still owed before the meeting
  completes, and logs any range it could not transcribe with its capture times.
- The meeting page says "Transcription delayed — catching up (Ns behind)" or "Finishing transcript…"
  instead of "no words" when speech is queued.
- `BOT_TRANSCRIPTION_LATENCY=low` turns on the low-latency profile (end-of-utterance finals,
  single-answer confirm, windows under 10 s, pipelined drafts on per-participant tracks).

How it works: the bot reports how far transcription is behind capture on the transcript stream; the
collector forwards it to the meeting feed without storing it.
Why it complies: audio and text stay inside the deployment's own transcription path; the progress
report carries only seconds, never audio or words.
