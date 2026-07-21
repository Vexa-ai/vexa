### Fixed

**The single-pass reference no longer counts its own chunk overlap twice.** Chunks are cut with 3s
of shared audio so a word split by the cut still appears — but that audio was transcribed in both
chunks, so the seam text landed in the reference twice. 122 words (3.2%) on the youtube control, and
every one scored as a miss against a live transcript that correctly said it once. Streaming loss
there was overstated as 9.5%; it is at most ~6.3%.
