- **A bot no longer stays in an emptied Teams meeting until the four-hour cap (#1673).** When the
  page reports connected remote audio streams and no audio reaches the bot, the bot suspects its own
  capture failed and holds `left_alone`. Before this fix that hold had no limit. On Teams the server
  mix stays advertised after the last person leaves, so every emptied Teams meeting held the bot
  until `maxActiveMs` and kept a quota slot busy. One continuous capture-fault hold now lasts at most
  three silence windows (clamped to 1–15 minutes, from `max_time_left_alone` /
  `BOT_ALONE_SILENCE_WINDOW_MS`; no new setting). After that the bot leaves as `completed(left_alone)`
  and logs `capture-fault hold EXPIRED`. Audible audio ends the incident, so a later fault gets a new
  hold. See [When the bot leaves on its own](/how-to/send-a-bot#when-the-bot-leaves-on-its-own).
