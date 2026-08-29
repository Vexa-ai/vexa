- **A dead Google Meet code now ends in seconds with its own reason, not a retried `join_failure`
  (#1325).** When Meet answers that a meeting space does not exist, it renders an error screen with
  no join control at all. The bot used to hunt that screen for a join button for the full 60s budget
  and report the generic `join_failure` — a reason the control plane classes transient, so it
  re-spawned bots against a code that can never exist. The screen is now detected before any button
  hunting and reported as the new `meeting_not_found` completion reason, which is permanent and
  never retried.
