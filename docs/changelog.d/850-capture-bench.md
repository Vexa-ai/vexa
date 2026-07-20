### Added

**Capture loss is reproducible with no meeting, no bot and no human.** `eval/src/capture-bench.mjs`
drives the real `createMixedAudioCapture` in a real browser over a synthetic source whose silence
structure and main-thread load are set by the bench, and reads the delivery counters. It settles
which of the two mechanisms — the silence gate or the main-thread ScriptProcessor — actually omits
audio, which no session recording can do on its own.
