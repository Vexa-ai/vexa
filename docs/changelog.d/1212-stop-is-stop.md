- **An explicit stop is honoured on every path (#1212).** Stopping a scheduled meeting before its
  bot is dispatched now cancels that occurrence instead of letting the sweep send a bot anyway; a
  stop that races a starting bot leaves nothing running; and a stopped meeting records the stop as
  its outcome rather than a join failure. A meeting the user stopped can no longer be continued in
  place — request a new bot instead.
