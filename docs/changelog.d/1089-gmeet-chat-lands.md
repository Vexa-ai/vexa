- **Google Meet chat: the bot's lines reach the meeting, and a line that does not is reported (#1089).**
  On a real Meet the bot typed `POST …/chat` lines into a hidden field instead of the chat, so nothing
  appeared while the bot logged each line as sent. The bot now opens the chat panel and writes only
  into its visible message box, and counts a line as posted only once it shows in the meeting's chat;
  otherwise its log says `chat_send_failed` with the reason. `POST …/chat` still answers `200 queued`.
  See [Meetings](/api/meetings).
