- **Chunked recording + in-browser playback for every meeting (#1015).** One ffmpeg per bot now
  records straight to live **HLS** (chunked fMP4/CMAF) — audio always, plus the meeting video when
  `RECORD_VIDEO=true` — the single recording path for both audio-only and A+V meetings. HLS plays
  **natively in Safari/iOS** (no JS, low battery) and via hls.js everywhere else, so a recording streams
  smoothly on mobile and shows its full length on load. Codecs are configurable — `RECORD_VIDEO_CODEC`
  (h264/hevc/vp9/av1) and `RECORD_AUDIO_CODEC` (aac/opus) compose with `VIDEO_HWACCEL` into an encoder
  matrix (impossible combos fail the spawn with a named error); defaults are **h264 + aac** for universal
  playback. The recording is **crash-resilient** — its HLS playlist is written incrementally as segments
  land, so even a bot that is hard-killed mid-meeting still leaves a playable recording on the server. An
  optional combined download (`ENABLE_COMBINED_RECORDING`) remuxes the HLS into one shareable mp4 with
  **audio and video kept in sync**, on demand or auto-built on completion (`AUTO_COMBINED_RECORDING`).
