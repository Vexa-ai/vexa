- **Calendars inside your own network, and organisations auto-join must never join.** An operator can
  now name internal hosts and networks that calendar feeds may be served from
  (`calendar.internalFeedAllow` in Helm, `VEXA_CALENDAR_FEED_ALLOW`); everything else internal stays
  refused, and loopback and cloud metadata can never be listed. A new auto-join block list, set for
  every account by the operator (`calendar.autoJoinBlock`, `VEXA_AUTO_JOIN_BLOCK`) or by each person
  (`auto_join_block` on `PUT /user/calendar`), stops the calendar bot from joining any meeting whose
  organiser, invitee or link host matches. Pushing meetings from your own calendar system with
  `POST /meetings` is now documented with an example. See
  [Calendar sync → Enterprise settings](/how-to/calendar-sync#enterprise-settings).
