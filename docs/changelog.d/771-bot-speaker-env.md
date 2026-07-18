- **Speaker-stream tuning now reaches spawned bots (#771).** `runtime.speakerStream.*`
  (`BOT_SPEAKER_MIN_AUDIO_SEC`, `BOT_SPEAKER_SUBMIT_INTERVAL_SEC`, `BOT_SPEAKER_CONFIRM_THRESHOLD`,
  `BOT_SPEAKER_MAX_BUFFER_SEC`, `BOT_SPEAKER_IDLE_TIMEOUT_SEC`) rendered onto the runtime pod are
  now merged into each spawned bot workload's environment, so the accuracy/latency knobs actually
  take effect on k8s and docker-backed deployments instead of rendering as dead config. See
  [Configuration](/configuration).
