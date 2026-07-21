### Fixed

**A speaker watcher that has gone blind now says so.** Its only output is a speaker transition, so
selectors that stop matching produce perfect silence — clean logs, a full transcript, and a speaker
column reading `seg_0, seg_4, seg_7`. Zoom's web client no longer renders any of the containers the
watcher looks for, and an entire live meeting was attributed to nobody without one line reporting it.
It now reports having seen no speaker, and names which of the two causes the DOM supports: a silent
room, or stale selectors.
