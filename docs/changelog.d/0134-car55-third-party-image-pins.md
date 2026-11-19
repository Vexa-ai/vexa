- **Every third-party image Vexa runs is pinned by digest.** Lite's postgres sidecar
  (`PG_IMAGE`) now runs the same `postgres:17-alpine` digest as Compose and the chart; the opt-in
  local STT sidecar (`WHISPER_IMAGE`) is pinned to the last CPU build of
  `fedirz/faster-whisper-server:latest-cpu`, which its project no longer publishes; the transcription
  stack's load balancer is pinned to a current `nginx:alpine` build that scans clean; and SearXNG's pin
  names its tag (`2026.9.5-c7f3080aa`) beside the same digest. A pull now gets the bytes that were
  scanned, never whatever a moving tag points at that day. Override any of them as before
  (`PG_IMAGE=…`, `WHISPER_IMAGE=…`, `SEARXNG_IMAGE=…`).
