- **A bot stopped by Google Meet's "Gemini is taking notes" dialog now says so instead of showing
  "joining" for 15 minutes.** When the bot meets a blocker before the waiting room, it asks for help
  (`needs_help`) right away, with the reason (`consent_required` for the Gemini dialog) kept in the
  meeting's status history. Before, that request was refused because the bot had not reached the
  waiting room yet: the meeting read "joining" until the admission budget ran out and then failed
  with no trace of why. If nobody helps, the meeting still fails as a join failure on our side, never
  as "the host did not let the bot in". A bot waiting for help is also no longer recorded as
  "completed" when its container dies or is stopped.
