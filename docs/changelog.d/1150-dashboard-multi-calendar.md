- **Manage multiple calendar feeds from the hosted dashboard (#1150).** Calendar is now a
  first-class sidebar page with named, independently synced and disconnected ICS connections.
  Meetings deduplicate across feeds, retain every named source under **Imported from**, and keep
  existing single-calendar API clients compatible. The page groups upcoming auto-joins by calendar,
  assigns each connection its own bot display name, and persists the complete source event metadata
  on the planned meeting. The Meetings page requests run history separately, so `idle` and
  `scheduled` calendar plans appear only in Calendar and no longer displace transcript rows. See
  [Calendar sync](/how-to/calendar-sync).
