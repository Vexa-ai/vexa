- **Jitsi: a bot no longer stops transcribing under continuous speech.** The mixed lane opened a
  turn only on a segmenter edge, so speech already in progress when the bot joined, a resumption
  after a pause shorter than the edge dedup, or a turn closed by the idle-finalize left audio
  arriving with nothing sent to STT — for minutes, with no error. The segmenter now emits the edge
  that brings its stream into line with what it hears, and the idle-finalize opens the next turn
  at once. A new deaf-lane guard reports a lane that hears audio and makes no STT round-trip for
  two minutes as `stt_fault` kind `lane_deaf` on the terminal lifecycle event, and logs it.
  Proven offline (mock STT, synthetic speech); not yet re-witnessed on a live soak.
