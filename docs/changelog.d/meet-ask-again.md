- **Google Meet: the bot asks to join again when Meet ends an unanswered request.** Meet ends a
  request to join that nobody answers after about 10 minutes, so a bot whose
  `max_wait_for_admission` was longer gave up early, with part of its wait unused. It now asks again
  while that wait lasts, and stops at once on a host's denial. A run that still ends in the lobby
  lists each request and when it was open in its reason text. See [Troubleshooting](/troubleshooting).
