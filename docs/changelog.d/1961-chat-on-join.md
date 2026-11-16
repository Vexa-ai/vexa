- **`POST /bots` takes `chat_on_join`, a line for the meeting chat (#1961).** An optional string
  (1-500 characters, not blank) stored on the meeting as `data.chat_on_join` with its `request_id`;
  blank, non-string, over-long, or on a `voice_note` / `vexa_call` answers `422` naming the field.
  Like `POST …/chat`, it is refused to a turn nobody is watching (`403 human_session_required`, a
  routine granted `bot.request` included: it may still send the bot without the line) and to a shared
  turn (`403 shared_conversation`). Requests without it are unchanged. See
  [Errors](/api/errors).
