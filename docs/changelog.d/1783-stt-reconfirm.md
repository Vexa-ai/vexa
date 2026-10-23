- **Show fast, then reconfirm (Google Meet, Zoom, calls).** Every transcribed window now appears at
  once as a draft, dimmed in the live transcript. When a speaker pauses, or 10 s of their speech
  accumulate, that stretch is transcribed again in one request with their earlier words as context.
  One confirmed segment then replaces the drafts everywhere: the stored transcript, the live feed
  and the meeting view. Short "…" fragments no longer remain in the transcript. Under transcription
  overload a reconfirmation waits for the service. If it cannot run, the drafts are kept as they
  are, so no words are lost. `BOT_TRANSCRIPT_RECONFIRM_SEC` sets the stretch (default `10`, `0`
  turns it off).
- `BOT_STT_CONFIDENCE_FILTER=strict` now applies only to requests of 8 s or more; on short windows
  it removed real words.
- This is now the default lane for meeting bots: pause-ended drafts within about a second, then
  reconfirmed segments. `BOT_TRANSCRIPTION_LATENCY=standard` restores the confirm-on-agreement lane.
