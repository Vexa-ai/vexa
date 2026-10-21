- **In-meeting chat is served by the API: read, send, and a live frame (#1089).**
  `GET /bots/{platform}/{native_meeting_id}/chat` now returns the chat your bot captured as
  `{sender, text, timestamp, is_from_bot}` rows, oldest first. Before, it always returned an empty
  list. `POST …/chat` with `{"text": "…"}` hands the line to your bot in the meeting and answers
  `200 {"status": "queued", …}`; it used to answer `405`. Each captured line also arrives over `/ws`
  as a `chat_message` frame, which nothing produced before. What a meeting actually captures or
  sends still depends on that platform's bot. See [Meetings](/api/meetings).
