- **Google Meet: a host denial now ends the meeting instead of hanging forever (#840).** Meet loads
  reCAPTCHA Enterprise invisibly on every join, so a background captcha frame sat on the denial
  screen too — the bot read it as bot-detection, logged "staying for manual/agent solve" every two
  seconds, and the meeting stayed `awaiting_admission` until the 10-minute lobby timeout (which is
  retried, so a host who said no could be knocked on again). An explicit denial ("denied your
  request", "weren't allowed to join", …) now wins over any captcha on the page, only a *visible*
  challenge widget counts as a real captcha, and the stay-for-solve wait is bounded at two minutes.
  A denied bot reports `failed / awaiting_admission_rejected` — permanent, no re-knock.
