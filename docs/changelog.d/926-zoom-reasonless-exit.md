- **A non-admitted bot death now carries its cause instead of `reason: None` (#926).** When the join
  ended without admission — a Zoom `auth_required` / `host-not-started` wall, a denial, a lobby
  timeout, or a browser that never launched — the bot emitted a terminal `failed` with the
  `completion_reason` enum but **no human `reason` text**, so meeting-api synthesized the
  uninformative `Bot exited with code 1; reason: None` (two Zoom users hit this back-to-back in prod).
  The typed `AdmissionError` message is now carried through the join driver (`JoinResult.reason`) and
  the orchestrator ALWAYS stamps a `reason` on the non-admitted terminal — falling back to a derived
  line so no reasonless terminal can leave that branch. The fix is platform-agnostic: any bot death
  through the non-admitted path now reaches the row with its cause attached.
