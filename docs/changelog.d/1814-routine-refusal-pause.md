- **A routine that keeps being refused stops instead of looping (#1814).** In a run, the second
  identical refusal tells the agent to stop and the third ends the run, with one friction record.
  Across runs, a scheduled routine refused for the same reason three runs in a row is switched off
  (`enabled: false`, with a `paused_reason`), its schedule is cancelled, and one item appears in
  what is waiting for you. Switching it back on resumes it. Chats are never paused. See
  [Brief me every morning](/how-to/daily-brief).
