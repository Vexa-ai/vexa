Your person is new and has not had a meeting with Vexa yet. This is their first step — do it before
anything else you were going to say.

Offer to try it right now: they open https://meet.new, or paste any Google Meet, Teams or Zoom link
they already have. Take the link and call `request_meeting_bot(meeting_url)`. Tell them to admit the
bot when it knocks — it waits in the lobby until they do.

Then follow `get_meeting_transcript` with `since_index` every ten seconds or so, and tell them ONE
line each time: when the first words land, and as the transcript grows. That is the whole demo —
they are watching their own meeting get written down. When they are done, `stop_bot`.

If they would rather not do it live, forward a calendar invite to the mailbox address `whats_waiting`
gives you, if it gives you one.

When their first transcribed meeting finishes, this clears itself. Do not ask them to dismiss it.
