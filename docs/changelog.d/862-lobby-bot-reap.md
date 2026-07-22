- **A bot waiting in a Google Meet lobby is no longer killed at 5 minutes (#862).** The bot is handed
  a 10-minute waiting-room budget, but it reports `awaiting_admission` once and then waits silently —
  so the reconcile sweep, which only checked whether the workload was alive for bots already *in* the
  meeting, force-deleted healthy bots at 300s of legitimate quiet, seconds before hosts who admit at
  4–5 minutes let them in. The liveness check now covers the whole pre-admission span, and the sweep's
  patience for a not-yet-admitted bot is derived from the budget it issued, so it can never be shorter.
- **A bot that never reached the meeting now reports why, and is retried (#862).** When the runtime
  confirms a pre-admission workload really is gone, the meeting is attributed to the stage it died in
  (`awaiting_admission_timeout` / `join_failure`) with the runtime's own evidence — workload state and
  exit code — instead of the catch-all `left_alone`. `left_alone` counts as a normal ending, so it also
  suppressed the automatic re-join; these reasons are retryable, and the re-spawn happens again.
