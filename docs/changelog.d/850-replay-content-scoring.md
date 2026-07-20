### Added

**A lane change can now be judged on content, not just structure.** A corpus entry stores the
transcript its ORIGINAL live session produced, so it can score that session and nothing else — the
moment code changes, "did this diff lose words?" was unanswerable. `TRANSCRIPT_OUT=` writes the
transcript a replay produces, and `eval/src/score_replay.py` scores it against either a corpus
entry's single-pass reference or a synthetic fixture's known text.
