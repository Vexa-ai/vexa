- **In-meeting chat on Google Meet: read, send, and a live frame (#1089).**
  The Google Meet bot now reads the meeting chat and can post into it, as the Jitsi bot already
  could read it. `GET /bots/{platform}/{native_meeting_id}/chat` returns the chat your bot captured
  as `{sender, text, timestamp, is_from_bot}` rows, oldest first; before, it always returned an empty
  list. `POST …/chat` with `{"text": "…"}` hands the line to your bot and answers
  `200 {"status": "queued", …}` on Google Meet and Jitsi; it used to answer `405`. Each captured line
  also arrives over `/ws` as a `chat_message` frame. See [Meetings](/api/meetings).
  The chat reader and sender do not depend on the language of Meet's interface: they find the chat
  panel, the message list, the message box and the send button by Meet's structure and attributes,
  not by English labels. `BOT_UI_LOCALE` (Compose and Lite) now reaches spawned bots, so a deployment
  can run its bots' browser in, for example, `nl-NL` or `de-DE`; see [Configuration](/configuration).
