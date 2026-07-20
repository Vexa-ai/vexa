### Fixed

**Mixed capture no longer discards time.** The peak-amplitude silence gate is off by default: a
dropped frame does not drop audio, it drops the timeline everything downstream reconstructs meaning
from. Measured on a real bot renderer, that gate accounted for the entire capture deficit while the
ScriptProcessor delivered every buffer it was given. The cost it was paying for is already paid
twice downstream, before Whisper and after it.
