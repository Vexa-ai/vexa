- **Google Meet: a refused bot is replaced by a new one instead of failing the meeting (#2006).**
  When Meet refuses a guest bot ("You can't join this video call"), the meeting now stays `joining`
  and Vexa starts a new bot for it about 30 s later, then 60 s after that: at most 3 bots, and none
  later than 15 minutes after the first. Stopping the meeting while the next bot is due ends it at
  once. Self-hosted: `VEXA_MEET_RESPAWN_PODS` (1 turns it off), `VEXA_MEET_RESPAWN_DELAYS_S` and
  `VEXA_MEET_RESPAWN_WINDOW_S` on meeting-api.
