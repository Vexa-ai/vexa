- **Fixed:** Teams/Zoom live transcripts now paint in real time on the dashboard instead of only
  after a reload. The bot's mixed-lane segment mapper wasn't stamping `absolute_start_time`, so the
  live renderer (which keys on it) skipped every pending draft; a prior producer-stamp fix had
  covered only Google Meet. ([#895](https://github.com/Vexa-ai/vexa/issues/895))
