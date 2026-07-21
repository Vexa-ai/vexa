### Fixed

**A transcript timestamp is wall time, even when the audio behind it has holes.** The mixed lane
concatenates only the frames that exist, so a span containing a hole yields audio shorter than the
span it covers; Whisper's segment times were then read as if that compressed audio were the wall
clock, stamping every word after a hole early by the accumulated hole. The cut now carries its span
layout and maps back through it. The audio stays compressed — zero-filling a hole only invites the
model to hallucinate over manufactured silence.

A span is also no longer allowed to reach behind the audio ring: `cut` can only return what it still
holds, so an over-long span returned recent audio under an old start time and put a whole turn at
the wrong instant.
