- **Meeting workers now terminate within a declared bound after an end verdict (#934).** Capture
  stop, recording finalization, and transcription-engine stop each report start, finish, or fault;
  a hung teardown can no longer hold `completed(left_alone)` indefinitely or consume a bot slot
  for hours. Recording still receives its own finalization budget and emits at most one final signal.
