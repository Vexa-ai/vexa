- **flows-api knows when an agent worker, not the person, is calling (#1784).** A worker's token
  resolves to the person it acts for together with its regime. A worker running with nobody in the
  loop can still read the person's queue, reactions, timeline and flows and file friction, but
  `reaction_signal` (retry, resume, wake or cancel a reaction) answers it `403 human_session_required`,
  the same refusal agent-api and meeting-api give.
