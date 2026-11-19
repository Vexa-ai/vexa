- **Routines have one mechanism: your own flows (#1783).** On upgrade, agent-api converts every
  `routines/<name>.md` file and every old routine job into your flows once, then stops. Files with
  the same intent become one routine. A file a run wrote with nobody watching arrives paused for you
  to resume. A file that cannot be read, or that flows refuses, runs nothing and is shown on the
  Routines board with the reason. Converted files leave the workspace, with a copy kept outside it,
  and a routine file written afterwards does nothing. There is no confirmation step. A routine
  refused for the same reason three runs in a row pauses and says why in its chat; alerts go to that
  chat unless you chose a working connection for them. `/api/routines` is a deprecated adapter onto
  flows until 0.14.0, and answers `501 routines_need_flows` on a deployment without flows. See
  [Routines and agent messages](/flows/routines#upgrading).
