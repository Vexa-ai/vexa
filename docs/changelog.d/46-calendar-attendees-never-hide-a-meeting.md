- **A calendar meeting can no longer disappear from the archive because of its invite list.**
  Calendar sync stores attendees as structured entries (email plus optional name), while the
  read plane only renders plain attendee strings — and it used to reject the *entire* meeting
  when it saw anything else. With calendar auto-join now shipping, the first calendar-synced
  meeting that got captured would have vanished from the archive completely: missing from the
  meeting list, unopenable directly, and leaving its transcript and summary stranded with no
  meeting to belong to. The meeting is now always kept; only attendee entries the archive
  cannot render are skipped. Nothing new is exposed — showing invitee names or email
  addresses is a separate decision, deliberately not made here.
