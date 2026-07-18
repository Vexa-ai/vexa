- **Authenticated bots: sessions survive use (#725).** The bot writes the rotated browser session
  back to the userdata store on clean teardown, so the next spawn restores the freshest state
  instead of a decaying snapshot; a second concurrent spawn against the same stored session is
  refused with a 409 naming the conflict (one identity, one live bot). The session-lifetime levers
  are documented on [Authenticated bots](/authenticated-bots).
