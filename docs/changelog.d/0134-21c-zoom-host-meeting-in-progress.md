- **The Zoom bot waits out "The host has another meeting in progress".** When the host's Zoom account
  is in another meeting, Zoom shows a page that reloads itself every few seconds instead of the
  name field. The bot used to give up on it after 30 seconds with a name-field timeout. It now waits
  for that meeting to end and then joins, for up to 10 minutes. If the page never opens, the run ends
  as `awaiting_admission_timeout` with the page's message, not as a join failure. A pre-join page the
  bot does not recognise ends after 30 seconds as `name_input_missing`, with the page's address,
  title and text in the reason.
