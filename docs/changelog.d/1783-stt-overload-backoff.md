- **Meeting bots back off from an overloaded transcription service instead of flooding it.** When
  the STT answers 429, 503, or a 5xx that relays an upstream rate limit, the bot now waits for the
  service's `Retry-After` (or a jittered backoff, 2 s up to 30 s) and pauses every request of that
  bot, not just the one that failed. After three overload answers in a row only one probe request goes
  out until the service answers again. A bot keeps at most 8 requests in flight, and never sends a
  window a second time while it is backing off (at most one duplicate per speaker when the STT is
  merely slow). Held audio is kept and sent in order once the service recovers. If a speaker's
  held audio passes the bound (300 s), the lost stretch is logged with its time range and is listed
  in the lane's missing ranges, so no loss is silent. Log lines: `[STT-OVERLOAD]`, `[SttGate]
  [BREAKER-OPEN]` / `[BREAKER-CLOSED]`.
