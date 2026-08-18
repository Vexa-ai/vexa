# Ground truth for the calibration meetings

One field: the pseudonym each virtual track's audio actually belongs to.
Everything the calibrator needs follows from it — a segment is truly mislabeled
iff its rendered label differs from its track's true owner, and a track is truly
mislabeled iff its dominant label does.

Truth here is not this tool's opinion. It comes from the offline tape replays
recorded in [#1221](https://github.com/Vexa-ai/vexa/issues/1221): csrc
separation, DOM-hint × CSRC purity, and a settled-window namer replay, three
independent lines agreeing.

Pseudonyms are meeting-local and reproduced by `judge.py prepare` from the same
roster; the name map that produces them never leaves the operator's machine.
