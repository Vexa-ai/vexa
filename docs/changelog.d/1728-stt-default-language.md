- **Self-hosted: pin the meeting language for every bot (`TRANSCRIPTION_LANGUAGE`, #1728).** Set it (e.g.
  `ru`) and every bot whose `POST /bots` names no `language` transcribes in that language instead of
  auto-detecting each window — a single-language meeting stops collecting short segments mislabeled
  as English, Spanish or Portuguese. An explicit `language` on the request still wins. Compose and
  Lite; on Helm use `extraEnv`. See [Configuration](/configuration#transcription-stt).
