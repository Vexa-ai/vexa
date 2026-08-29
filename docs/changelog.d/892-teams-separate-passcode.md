- **Teams bots join with a separate `passcode` (#892).** A numeric Teams meeting ID plus its
  `passcode` now builds the join URL Teams actually uses — `…/meet/<id>?p=<passcode>` — instead of
  interpolating the id into the thread-id deep link and dropping the passcode; a
  `19:…@thread.v2` id keeps its `…/l/meetup-join/` path. `teams_base_host` is honoured for the
  personal and GCC-High/DoD clouds (unknown hosts are a `422`), and the passcode is kept off the
  stored `constructed_meeting_url` and out of the bot's logs. Unsupported password aliases
  (`password`, `meeting_password`, …) are refused with a typed `422` naming `passcode` rather than
  accepted and silently ignored. See [Send a bot](/how-to/send-a-bot).
