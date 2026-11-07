- **The per-kind tool-call budgets and the job ceilings reach the worker (#1784).**
  `VEXA_AGENT_MAX_TOOL_CALLS_CHAT`, `_JOB`, `_ROOM`, `_FLOW`, `VEXA_AGENT_JOB_MAX_TOOL_CALLS` and
  `VEXA_AGENT_JOB_MAX_TURN_SEC` are forwarded by the runtime into every agent worker and declared in
  its configuration; before, a deployment's setting never left the runtime. A chat turn that stops at
  its budget after continuing now reports the whole turn's steps and ceiling. The agent's connection
  request no longer accepts `github`: Git tokens are added by the person in Connections → Git. See
  [Agent tool calls](/agent-tool-calls).
