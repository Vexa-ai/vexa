- **Authenticated bots: the user flow exists end to end (#724).** Provision a signed-in bot session
  with one command (`make login` — sign in once, the session lands in the deployment's userdata
  storage), then set `BOT_AUTHENTICATED=true` on meeting-api and every stock `POST /bots` spawns
  signed-in under the account identity. A failed session restore is now a typed, attributed
  `session-restore` error instead of an unattributed pre-launch death. See
  [Authenticated bots](/authenticated-bots).
