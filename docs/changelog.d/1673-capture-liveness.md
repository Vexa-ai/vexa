- **A bot alone in a silent Teams meeting leaves after `max_time_left_alone` again (#1673).** Capture
  now reports that it is running separately from whether anyone is audible, so an empty room whose
  meeting audio stream stays connected is no longer mistaken for a broken capture and held open until
  the deaf-capture bound. Silent audio is still never sent to transcription. See
  [Send a bot](/how-to/send-a-bot).
