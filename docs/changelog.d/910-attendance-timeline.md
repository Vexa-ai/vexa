- **Meetings now record attendance, not just the transcript.** Every completed meeting carries who
  was in the room and for how long — `data.attendance.participants[]` with each person's
  `first_seen`, `last_seen`, `present_seconds`, and the intervals they were actually present for, so
  a late joiner and an early leaver read differently from someone there throughout, and a
  leave-and-rejoin never counts the time away. The bot reads the roster its capture modules already
  maintain for speaker attribution (no new scraping, no OCR) and reports it once, on the terminal
  lifecycle event. The meeting header's participant count is now real, and the meeting canvas gains
  an **Attendance** table. Google Meet, Zoom, Teams and Jitsi; older meetings simply have no
  attendance record.
