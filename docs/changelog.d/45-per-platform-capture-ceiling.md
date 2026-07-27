- **Operators can cap capture length per platform.** `ZAKI_MINUTES_PLATFORM_CAPTURE_SECONDS`
  (for example `teams=3600,zoom=3600`) sets a per-platform ceiling below the deployment
  maximum. This matters because only Google Meet currently detects that everyone has left a
  meeting — on the other platforms a bot whose participants all leave stays until the
  ceiling and bills the whole way, so raising the global maximum raised that exposure with
  it. A lower per-platform cap bounds the worst case now, without waiting for a participant
  detector on each platform. Unset changes nothing: every platform keeps using the
  deployment maximum. A ceiling above the deployment maximum, below the 60-second floor, or
  naming an unknown platform refuses to start rather than silently reverting to the maximum.
