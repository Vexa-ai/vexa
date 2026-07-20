### Fixed

**The speaker-cardinality signal weights by how long each name was lit.** It compared distinct hint
names against distinct published speakers with no weighting, so a cough, a "yeah", or a poll landing
on the wrong tile made someone a participant the transcript then appeared to lose. On a real Zoom
call three people lit for 6s, 4s and 2s of 269s produced a confident finding that was simply wrong.
