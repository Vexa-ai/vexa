- **Bot lifecycle callbacks survive meeting-api blips; completions carry a delivery marker (#806, #807).**
  The bot's status-callback retry horizon grows from ~0.6s (3×200ms) to ~7.5s (5×500ms exponential) —
  in hosted production a brief meeting-api disruption made seated, healthy bots unable to report
  `joining`, and the reaper then failed their meetings; a longer horizon rides out such blips.
  Separately, every terminal transition now stamps `meeting.data.segments_captured`, so a meeting
  that "completed" without capturing any transcript is queryable and alertable instead of being
  indistinguishable from a success.
