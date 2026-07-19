### Fixed

- **Zoom and Teams admission verdicts are now typed, so permanent failures are no longer
  retried on your quota.** Both platforms threw plain errors for every admission failure, which
  the control plane classified as transient `join_failure` and re-spawned up to 3× — a Zoom host
  denial re-knocked on the same host, the Zoom RTMS anti-bot wall was retried into the same wall,
  and a meeting restricted to signed-in users was rejoined just as signed-out. They now throw the
  same typed `AdmissionError` Google Meet and Jitsi already use: denials and auth walls are
  permanent (no retry), lobby timeouts stay transient (still retried). Teams additionally had an
  outer catch that re-wrapped *any* error — including a typed one — into a plain string; typed
  verdicts now pass through it. Failure reasons land in `completion_reason`
  (`awaiting_admission_rejected` / `awaiting_admission_timeout` / `auth_session_missing`), making
  these modes measurable per platform for the first time.
  ([#806](https://github.com/Vexa-ai/vexa/issues/806))
