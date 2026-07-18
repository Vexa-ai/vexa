- **Google Meet authenticated join: fail closed on signed-out browser profile (#607).** When
  `authenticated: true` the bot refuses to degrade to an anonymous join if the browser profile
  is signed out — the guard throws a typed `AuthSessionError` (an `AdmissionError` subclass with
  outcome `auth_session_missing`) which the join driver maps to the new permanent
  `auth_session_missing` completion reason: the control plane records the truth and never
  re-spawns against the dead profile. `auth_session_missing` is added to the lifecycle.v1 and
  api.v1 completion-reason enums (api.v1 also gains the previously missing `startup_alone`;
  contracts re-sealed, lane:contract). The guard distinguishes a signed-out guest lobby (name
  input visible) from a signed-in-but-not-pre-admitted account (no name input), which still
  knocks via "Ask to join".
