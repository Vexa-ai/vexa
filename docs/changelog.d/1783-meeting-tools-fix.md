- **Meeting tools answer what is true (#1783).** `transcript_terms` now finds people and companies a
  meeting names by one word ("ask Maria", "Siemens wants the pilot"), in any cased script; before,
  a meeting that used first names came back with no terms. `stop_bot` on a meeting that has already
  stopped answers success with `already_stopped` (or `already_stopping` while the bot is leaving)
  and the meeting's status, instead of 404; a meeting you never had is still a 404. Reading the
  transcript of a meeting whose owner deleted it answers 410 `transcript_deleted` to anyone who can
  see the meeting, instead of "Meeting not found" for a meeting that is still in your list.
