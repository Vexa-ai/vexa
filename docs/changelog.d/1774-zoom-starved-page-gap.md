- **Zoom: a starved bot page no longer drops most of a call's speech (#1774).** Zoom's per-track
  frames carry capture-callback timestamps, and the per-channel lane read late or lost callbacks as a
  break in speech, discarding the buffered audio every few seconds. The lane now measures that gap
  between consecutive frames, so a continuous turn reaches transcription whole.
