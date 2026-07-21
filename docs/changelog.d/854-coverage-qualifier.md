### Fixed

**`coverage` in a corpus lane block is marked as harness-relative.** It sits downstream of
submission cadence, which an unpaced replay does not reproduce, so it is a regression detector
against the same fixture's own baseline — never the share of a meeting the lane covers. Measured
both ways on the same audio: the live session covered 0.808 of its span, a replay of it 0.378.
