- **The fake camera source is now a decodable y4m, not `/dev/null` (#998).** Both launch-arg lists
  pointed `--use-file-for-fake-video-capture` at `/dev/null`, which Chromium registers as a camera
  it cannot decode — Google Meet showed a permanent "Camera not found / Make sure your camera is
  plugged in" toast bottom-center over the meeting stage for the whole call, overlapping shared
  screen content. The bot entrypoint now generates a one-frame black `blank-camera.y4m` before the
  worker starts, and both flag sites point at it instead.
