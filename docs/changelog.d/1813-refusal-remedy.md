- **A refused unattended run says what works, and a routine that needs your mail says so when saved
  (#1813).** `human_session_required` now carries `remedy: ask_in_chat` and a `tell_your_person`
  sentence the agent relays: the run started without you, mail, calendar and connection actions
  only work when you ask in chat, and signing in again changes nothing. A routine whose prompt
  reads mail or the calendar is still saved, and its card and the create answer carry
  `needs_person` and a warning. See [Brief me every morning](/how-to/daily-brief).
