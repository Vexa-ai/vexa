- **In-meeting chat on Google Meet: read, send, and a live frame (#1089).**
  The Google Meet bot now reads the meeting chat and can post into it, as the Jitsi bot already
  could read it. `GET /bots/{platform}/{native_meeting_id}/chat` returns the chat your bot captured
  as `{sender, text, timestamp, is_from_bot}` rows, oldest first; before, it always returned an empty
  list. `POST …/chat` with `{"text": "…"}` hands the line to your bot and answers
  `200 {"status": "queued", …}` on Google Meet and Jitsi; it used to answer `405`. Each captured line
  also arrives over `/ws` as a `chat_message` frame. See [Meetings](/api/meetings).
  The chat reader and sender find the chat panel, the message list, the message box and the send
  button by Meet's structure and attributes, not by English labels.
- **The meeting bots' browser runs in English on every platform.** Chrome `--lang`, `Accept-Language`,
  the page locale and, on Google Meet, `hl=en` are pinned to `en-US`; there is no setting for it. After
  the meeting page loads the bot checks the language the page rendered in. A page in another language
  (a signed-in bot account set to another language) is logged as `ui_locale_mismatch`, and a join that
  then fails ends with `join_evidence.reason: ui_locale_mismatch` rather than a timeout or rejection
  the bot could not read. Set bot accounts to English; see [Authenticated bots](/authenticated-bots).
