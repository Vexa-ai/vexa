- **Meeting routes forward to exactly the route they matched (#1784).** On the `{platform}/{native_meeting_id}`
  routes the gateway now answers `422` for a `platform` outside `google_meet`, `zoom`, `teams`, `jitsi` and
  `browser_session`, and `400` for a `.` or `..` meeting id or an encoded `/` or `\` in the path, before the
  request is authorized; any other character in a meeting id, `?` and `#` included, is passed on as part of
  the id. The calendar sync routes (`/user/calendars/{id}/sync`) refuse a `.` or `..` id the same way.
  meeting-api also checks the key's scopes against the route a request reached, so a `tx`-only key is
  refused on bot routes there as well as at the gateway.
