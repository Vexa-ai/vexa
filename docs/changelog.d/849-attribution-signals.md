### Added

**Speaker attribution is scored without anyone labelling a meeting.** Four signals from the
pipeline's own outputs — words published under a provisional cluster id, the binder's per-hint
matched/missed verdict, turns whose name changed its mind, and published speakers against hinted
names — now ride in the lane block of every corpus entry, so an attribution regression fails
`score-fixture` the same way a duplicated sentence does.
