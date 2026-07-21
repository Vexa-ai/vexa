### Fixed

**A fixture can no longer be promoted against the wrong meeting's transcript.** `promote-fixture`
refuses a `--transcript` whose `native_meeting_id` differs from the session's, and refuses an empty
one. Pairing them by hand produced two confident wrong conclusions in a single day — a live
transcript from one Zoom call scored against a replay of another, and a reference scored against a
store that had restarted.
