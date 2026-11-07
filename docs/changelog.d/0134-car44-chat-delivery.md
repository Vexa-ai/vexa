- **A chat line sent into a meeting now says whether it posted.** `POST
  /bots/{platform}/{native_meeting_id}/chat` answers a `request_id`, and `GET
  /bots/{platform}/{native_meeting_id}/chat/{request_id}` reads the outcome the bot reports: `posted`
  once the line shows in the meeting's chat, `failed` with the reason (for example `not_landed`), or
  `unconfirmed` when no answer came within 60 seconds. Only the sender can read it. See
  [Meetings](/api/meetings).
