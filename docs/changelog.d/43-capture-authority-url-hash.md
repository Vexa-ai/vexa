- **Meeting links carrying a query string now work (#43).** A capture whose meeting URL had any
  query component was rejected with a generic `422 invalid_request` that looked exactly like a
  malformed link. That silently blocked **every Zoom invite link** (they always carry `?pwd=`) and
  **every enterprise Microsoft Teams link** (`/l/meetup-join/…?context=…`); only query-less URLs such
  as `meet.google.com/abc-defg-hij` ever succeeded, which is why the fault stayed hidden. The
  capture grant was bound to the query-stripped *dedup identity* while the capture service verified
  it against the *navigation URL* — the two disagreed whenever a query was present. The grant is now
  bound to the navigation URL, i.e. exactly what the bot opens. Meeting **deduplication is
  unchanged**: the native meeting id still derives from the query-stripped identity, so two `?pwd=`
  variants of one meeting remain a single meeting.
- **US-gov Zoom hosts (`zoomgov.com`) are accepted by the navigation gate.** The sealed
  `zaki-control.v1` predicate already admitted them, but the shared `bot_spawn` host check did not,
  so a gov Zoom URL passed the control gate and was then refused — the same gate-disagreement
  previously fixed for enterprise and gov Teams hosts.
