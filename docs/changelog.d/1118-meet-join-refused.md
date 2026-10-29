- **Google Meet: a refused join no longer waits in a lobby it never entered (#1110, #1118).** When Meet
  answers "Ask to join" with "You can't join this video call", the bot used to read that screen as the
  waiting room and report `awaiting_admission` for its whole lobby budget, although the host had
  nothing to admit. The bot now asks again, up to 3 times (Meet's refusal is intermittent), and if
  Meet keeps refusing it fails within seconds as `awaiting_admission_rejected` with the reason
  `google_meet_join_refused`, attributed to the platform rather than to the host or to Vexa.
