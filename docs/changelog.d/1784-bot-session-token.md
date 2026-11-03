- **A meeting bot holds only its own session's token (#1784).** The bot's invocation no longer
  carries the service-tier `INTERNAL_API_SECRET`. Its lifecycle callbacks, recording chunks and
  signal tapes authenticate with the MeetingToken minted for its session, which meeting-api now binds
  to that session (`session_uid`) and accepts for that session only; the lifecycle callback, which
  took no credential before, requires it. Upload credentials are compared in constant time. Bots and
  workers no longer receive model credentials or credential-file mounts on any backend — only agent
  workers do.
