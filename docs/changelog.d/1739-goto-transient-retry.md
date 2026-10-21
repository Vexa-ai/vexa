- **The bot retries its first navigation on a transient network error (#1739).** A single dropped
  socket while loading the meeting page (`net::ERR_CONNECTION_CLOSED`, `ERR_SOCKET_NOT_CONNECTED`
  and similar transport errors) no longer ends the bot with `join_failure`: Google Meet, Teams,
  Zoom and Jitsi now retry the page load up to three times, 1 s then 3 s apart. A closed browser or
  any non-network error still fails at once, and a path that stays broken still fails as
  `navigation_failure`, after the third attempt.
