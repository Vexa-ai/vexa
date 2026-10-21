- **Meetings now say how a run ended: `outcome_class` and `outcome_reason` (#1655).** Every bot run
  is graded when it ends as `ours`, `host_or_platform`, `user`, `normal` or `unknown`; only `ours`
  is a Vexa fault. A bot the host never admitted now reads «Not admitted by host» in the dashboard
  and in the API, not «Failed». See [Completion reasons](/troubleshooting/completion-reasons).
