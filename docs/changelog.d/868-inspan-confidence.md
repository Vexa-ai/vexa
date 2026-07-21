### Fixed

- **mixed lane (Zoom/Teams/YouTube): a handover turn the UI already named no longer publishes
  provisional when a neighbour's slack overlap dilutes the vote.** The window-match confidence gate
  scored a name's share of *all* hint time in the ±support slack. With sub-second diarizer turns, a
  handover commit fully covered by one speaker could still fail the 0.6 gate because the previous
  speaker's lingering/heartbeat slices sat inside the slack and split the share. Confidence now
  measures contested-ness *inside the commit span itself* — the slack keeps its jitter roles
  (admission, support, coverage) and loses its vote; when no hint time falls in-span at all (pure
  lag), the slack-window share judges as before. A committed regression test
  (`handover.smoke.test.ts`) pins the handover scenario red on the parent, green on the fix.
