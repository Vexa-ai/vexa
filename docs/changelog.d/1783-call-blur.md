- **Calls: background blur.** A Blur button on your own video tile (and in the call bar's "…" and before
  joining) cycles off / light / strong; the choice is kept and applied whenever your camera starts. It
  runs in the browser (`@livekit/track-processors` 0.8.1, MediaPipe) with the runtime and model served
  by the deployment at `/mediapipe/` — no CDN. An unsupported browser or a device that cannot keep up
  turns it off with a notice; the camera never freezes.
