- **Repeated friction reports fold into one row with a count (#1811).** When the same person files
  the same report (same tool, same refusal reason or same text once ids and timestamps are removed)
  within one UTC hour, `report_friction` no longer stores a new row. It increases `occurrences` on
  the first report and tells the reporter not to file it again. `friction_so_far` shows
  `occurrences` and `last_seen`. See [Flows operations](/flows/operations).
