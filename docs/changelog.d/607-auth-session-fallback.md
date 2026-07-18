- **Google Meet authenticated join: fail closed on signed-out browser profile (#607).** When
  `authenticated: true` the bot now refuses to degrade to an anonymous join if the browser profile
  is signed out — instead it throws a typed `AuthSessionError("auth_session_missing")` that the
  orchestrator maps to a permanent failure. The guard distinguishes a signed-out guest lobby
  (name input visible) from a signed-in-but-not-pre-admitted account (no name input), which still
  knocks via "Ask to join".
