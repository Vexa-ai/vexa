- **An agent turn's commits say an agent wrote them.** Each commit a worker turn makes stays authored
  by the person it runs for, and now ends with `On-behalf-of: <person>`, `Written-by: agent` and
  `Agent-unit: <unit>` trailers, taken from the dispatch (`VEXA_ON_BEHALF_OF` for a room's agent,
  else `VEXA_OWNER`, and `VEXA_UNIT_ID`). Text the model writes into the commit body cannot replace
  them: they are always the message's last paragraph.
