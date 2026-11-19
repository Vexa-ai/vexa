- **A bot's upload token works only for its own session (#1784).** meeting-api now refuses a recording
  chunk or signal tape whose MeetingToken is bound to another session or to none, as the lifecycle
  callback already did; a token is minted for one session only. The lifecycle callback and the
  runtime's callback refuse every caller when meeting-api was started without the key that checks
  them. Upgrade bots and meeting-api together (v0.13.2 or later on both).
