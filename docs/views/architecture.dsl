# GENERATED from architecture.calm.json — do not edit (pnpm arch:dsl --write)

system meetings  # capture → transcribe → record; owns the raw transcript
  service bot
  service desktop
  service meeting-api
  service mcp
  module buffer
  module capture-codec
  module gmeet-capture
  module gmeet-pipeline
  module jitsi-capture
  module join
  module mixed-capture-core
  module mixed-pipeline
  module record-chunker
  module recording
  module remote-browser
  module teams-capture
  module whisper
  module zoom-capture
  contract acts.v1
  contract captured-signal.v1
  contract flagged-issue.v1
  contract invocation.v1
  contract lifecycle.v1
  contract transcript.v1
  contract webhook.v1
  contract zaki-read.v1
  service transcription
  data-asset segments-stream [writers: bot]
  data-asset tc-stream [writers: meeting-api]
  data-asset tc-mutable [writers: bot, meeting-api]
  data-asset bm-status [writers: meeting-api]
  data-asset u-meetings [writers: meeting-api]
  data-asset bot-commands [writers: meeting-api]
  database segments-table [writers: meeting-api]
  data-asset recording-blob [writers: bot, meeting-api]

system agent  # copilot; owns the processed (cleaned) transcript + signals
  service agent-api
  contract event.v1
  contract invoke.v1
  contract proactive-card.v1
  contract routine.v1
  contract task.v1
  contract tool.v1
  contract unit.v1
  contract processed-notes.v1
  contract workspace.v1
  service agent-worker
  data-asset out-stream [writers: agent-worker]
  data-asset unit-in
  data-asset proc-stream [writers: agent-worker]
  data-asset va-chat

system gateway-system  # the one public edge (api.v1, ws.v1)
  service conformance
  service gateway
  contract api.v1
  contract logevent.v1
  contract ws.v1

system identity  # access + audit; owns the durable DB
  service admin-api
  contract identity.v1
  data-asset identity-db [writers: admin-api]

system runtime-system  # workload spawn (bot/agent containers)
  contract runtime.v1
  contract schedule.v1
  service runtime

system deploy  # deployment + execution-target registry
  contract execution-targets.v1
  contract config.v1

system platform  # shared infra backing the services
  service redis
  database postgres
  service minio

edges:
  bot -write-> segments-stream
  bot -write-> tc-mutable
  meeting-api -read-> segments-stream
  meeting-api -write-> tc-mutable
  meeting-api -write-> segments-table
  meeting-api -write-> tc-stream
  agent-api -read-> tc-stream
  gateway -read-> tc-mutable
  terminal -read-> tc-stream
  terminal -read-> proc-stream
  terminal -read-> out-stream
  bot -write-> recording-blob
  gateway -read-> recording-blob
  bot -call-> transcription  # audio -> first-party STT via TRANSCRIPTION_SERVICE_URL
  bot -read-> bot-commands  # SUBSCRIBE acts.v1 commands
  meeting-api -write-> bm-status  # PUBLISH status
  meeting-api -write-> u-meetings  # PUBLISH per-user status
  meeting-api -write-> bot-commands  # PUBLISH leave/speak
  meeting-api -write-> recording-blob  # S3 PUT stitched master
  meeting-api -write-> postgres
  meeting-api -write-> minio
  meeting-api -req-> runtime  # POST /workloads spawn bot
  agent-api -read-> segments-stream  # XREADGROUP agent_copilot (proactive watcher)
  agent-api -req-> runtime  # POST /workloads spawn agent-worker
  agent-api -read-> out-stream  # SSE relay (/api/chat, /api/meeting/stream)
  agent-worker -read-> tc-stream  # copilot tails transcript
  agent-worker -write-> out-stream  # XADD cards/notes/deltas
  agent-worker -write-> proc-stream  # XADD cleaned 1:1 notes
  agent-worker -read-> unit-in  # chat path XREADs interactive input
  mcp -req-> gateway  # every MCP tool forwards the caller's X-API-Key to the public REST surface
  gateway -req-> meeting-api  # proxy /bots /transcripts /meetings /recordings
  gateway -req-> agent-api  # proxy /agent/*
  gateway -req-> admin-api  # POST /internal/validate (authz oracle)
  gateway -read-> bm-status  # WS fan-out
  gateway -read-> u-meetings  # WS auto-subscribe
  gateway -read-> va-chat  # WS fan-out
  admin-api -write-> identity-db
  admin-api -write-> postgres
  terminal -req-> gateway  # all REST via gateway
  terminal -req-> gateway  # live WS via gateway
  slim -req-> gateway  # Python client; REST via gateway
  extension -req-> gateway  # browser extension client; live WS via gateway
  bot, agent-worker deployed-in runtime
  gateway, meeting-api, agent-api, admin-api, runtime, redis, postgres, minio, transcription deployed-in deploy

flows:
  live-transcript-flow: bot-writes-segments-stream -> collector-reads-segments -> collector-writes-tc -> aw-tcnative -> aw-proc -> terminal-reads-processed
  dispatch-flow: aa-runtime -> workers-deployed -> aw-unitout -> aa-unitout
