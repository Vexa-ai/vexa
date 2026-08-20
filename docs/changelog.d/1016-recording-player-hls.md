- **In-app recording player with HLS streaming + transcript sync (#1016).** A completed meeting plays
  its recording right in the meeting view — native `<video>` HLS in Safari/iOS and hls.js in Chrome/
  Firefox, so the full length shows on load (no scrubbing to the end) and audio-only meetings play too.
  The transcript follows playback and clicking a line seeks the player, anchored to the recording's true
  start. When `ENABLE_COMBINED_RECORDING` is on, a download button appears (and shows "Preparing
  download…" while the combined mp4 builds); it stays hidden when the download is disabled.
