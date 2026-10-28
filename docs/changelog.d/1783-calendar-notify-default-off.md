- **Calendar invitations are not emailed by default (#1783).** `calendar_notify_attendees` is `off`
  by default again: an agent creates and changes events without Google emailing the attendees, and
  asking for it is refused. A company that wants agents to send invitations sets it `on` in
  `_global/POLICIES.md`.
