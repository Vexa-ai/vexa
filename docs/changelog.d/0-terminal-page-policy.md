- **Terminal pages name every source they load from.** The terminal's Content-Security-Policy now
  starts from `default-src 'self'` and lists connections, images, styles, fonts, media and workers
  explicitly, with no wildcards. A third party appears only when you turned on its feature: your
  LiveKit server (`LIVEKIT_URL`, now also passed to the terminal by Compose and Helm) and Google
  Analytics (`GA_MEASUREMENT_ID`). A Compose override or Helm `extraEnv` that runs calls without
  the shipped files must give the terminal `LIVEKIT_URL` too, or the browser cannot join a call.
  See [Calls](/minutes/calls).
