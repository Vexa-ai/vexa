- **Zoom transcripts now name who spoke (#538).** The Zoom active-speaker watcher
  (`createZoomSpeakers`) is wired into the bot's page-side capture bundle: active-speaker
  transitions cross to the name binder as `dom-active` hints, so Zoom bot segments carry the
  real participant's name instead of `seg_N` placeholders — the same attribution Google Meet
  and Jitsi already deliver. Flicker-debounced (a single ~250 ms tile blip never mislabels a
  turn); screen-share layouts and nameless tiles emit no hint rather than a wrong one.
