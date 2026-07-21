### Fixed / Added

**A witnessed session now stays witnessed.** Recorded sessions become corpus entries — the signal
plus the numbers it produced at the time — so a defect fixed once keeps a test that fails when it
comes back. `eval/src/promote-fixture.mjs` makes an entry from a desktop tape or a bot's
captured-signal session; `eval/src/score-fixture.mjs` re-measures it and fails on drift. The index
is `eval/CORPUS.md`; the audio stays out of the repo.

The mixed lane's quality harness now models the CONSUMER — every published segment and every
pending tail upserted by `segment_id`, last write wins — which is the only place a draft-identity
defect is visible, and it runs the real segmenter on fixtures that carry no recorded cuts.
