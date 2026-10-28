- **A meeting takes at most 10 chat messages every 10 seconds.** `POST
  /bots/{platform}/{native_meeting_id}/chat` past that answers `429` with a `Retry-After` header, and
  the line is not sent. The count is the meeting's, whoever posts, so a looping client cannot flood
  the people in the call. See [Meetings](/api/meetings).
