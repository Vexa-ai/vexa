- **L3.5 wav→words value leg lands in CI (#560).** `make -C core/meetings/eval counting-ci`
  drives committed counting audio (1..20, speakers A/B) through a LOCAL CPU whisper
  (`deploy/transcription/docker-compose.cpu.yml`, model `tiny`), the real collector, and the
  API-served transcript, with the 1..N oracle attributing any drop to its stage and an STT-down
  negative control. Wired as `pr-value-stt` (transcription-path PRs) and `validate-counting-stt`
  (every release — guarantee row 3 is now machine-proven at its honest scope: local CPU STT
  audio→words, not real-Meet admission or hosted-STT capacity).
