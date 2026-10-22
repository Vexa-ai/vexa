# GENERATED from architecture.calm.json — do not edit (pnpm arch:dsl --write)

system meetings  # capture → transcribe → record; owns the raw transcript
  service bot
  service desktop
  service meeting-api
  module buffer
  module capture-codec
  module gmeet-capture
  module gmeet-pipeline
  module jitsi-capture
  module join
  module mixed-capture-core
  module mixed-pipeline
  module no-image-backend
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
  contract service-authority.v1
  contract session-profile.v1
  contract transcription-language.v1
  contract transcript.v1
  contract webhook.v1
  service transcription
  data-asset segments-stream [writers: bot]
  data-asset tc-stream [writers: meeting-api]
  data-asset tc-mutable [writers: bot, meeting-api]
  data-asset bm-status [writers: meeting-api]
  data-asset u-meetings [writers: meeting-api]
  data-asset bot-commands [writers: meeting-api]
  database segments-table [writers: meeting-api]
  data-asset recording-blob [writers: bot, meeting-api]
  data-asset userdata-blob [writers: remote-browser, meeting-api]
  contract sdk-join.v1
  module zoom-sdk-capture
  contract sdk-capture.v1
  data-asset acl-bots-index [writers: meeting-api]
  data-asset meeting-bundle-file [writers: meeting-api]
  data-asset meeting-bundle-parts [writers: agent-api]

system agent  # the execution domain: a trigger becomes one governed agent turn over a workspace.v1 git repo; owns no transcript
  service agent-api
  contract event.v1
  contract invoke.v1
  contract proactive-card.v1
  contract blocks.v1
  contract desktop.v1
  contract home-card.v1
  contract home-customer.v1
  contract routine.v1
  contract task.v1
  contract tool.v1
  contract models.v1
  contract unit.v1
  contract credential-broker.v1
  service credentials-broker
  data-asset credentials-store [writers: credentials-broker]
  service agent-worker
  data-asset model-grants [writers: agent-api, model-proxy]
  data-asset model-routes [writers: agent-api]
  service model-proxy
  data-asset out-stream [writers: agent-worker]
  data-asset unit-in [writers: agent-api]
  data-asset va-chat [writers: meeting-api]
  data-asset imports-status [writers: agent-api]
  data-asset onboarding-research-state [writers: agent-api]
  data-asset rail-order [writers: agent-api]
  data-asset rail-marks [writers: agent-api]
  data-asset redis-acl-users [writers: agent-api, meeting-api]
  data-asset acl-units-index [writers: agent-api]
  data-asset routine-state [writers: agent-api]
  data-asset delegation-revoked [writers: agent-api]
  data-asset delegation-live [writers: agent-api]
  data-asset delegation-records [writers: agent-api]
  data-asset delegation-current [writers: agent-api]
  data-asset unit-delegation [writers: agent-api]
  data-asset unit-fault [writers: agent-api]
  data-asset session-store [writers: agent-api]

system gateway-system  # the one public edge (api.v1, ws.v1)
  service conformance
  service gateway
  contract api.v1
  contract logevent.v1
  contract gateway-identity.v1
  contract ws.v1
  contract routes.v1
  service mcp
  contract mcp.tools.v1

system identity  # access + audit; owns the durable DB
  service admin-api
  contract identity.v1
  contract signin.v1
  data-asset identity-db [writers: admin-api]
  contract delegation.v1
  data-asset signin-link-redeemed [writers: admin-api]

system runtime-system  # workload spawn (bot/agent containers)
  contract runtime.v1
  contract schedule.v1
  service runtime

system deploy  # deployment + execution-target registry
  contract execution-targets.v1
  contract config.v1
  contract outbound-url.v1
  contract meeting-bundle.v1

system service-authority-system  # optional operator-owned admission and active-service authority; absent in stock OSS and never owns billing policy inside core
  service service-authority

system system-webhook-system  # optional operator-owned terminal-event consumer; absent in stock OSS and never selected from customer or meeting data
  service system-webhook

system platform  # shared infra backing the services
  service redis
  database postgres
  service object-store
  service livekit

system flows  # the reaction engine; owns the reaction row and its effect receipts
  module flows-engine
  module flows-defs
  module flows-steps
  module flows-schema
  module flows-timeline
  service flows-api
  service flows-worker
  data-asset flows-rows
  contract flows.v1

system workspaces-system  # the workspace domain: a person's git repo of durable memory and the contract that addresses it; hosted as a library inside agent, with no runtime of its own (P10)
  contract workspace.v1

system crm  # ADR-0047: the optional CRM — the organisation's stable entities beside workspaces; owns the crm_* tables
  service crm-api
  contract crm.v1
  data-asset crm-records [writers: crm-api]

system signals  # transcript signals: a small company-chosen model listens to live sources (a meeting's verified transcript first) against the listening brief the meeting's agent wrote, and wakes the agent through the company's flow; owns the sig:* keys
  service signals-service
  contract signals.v1
  data-asset sig-annotations [writers: signals-service]
  data-asset signals-state [writers: signals-service]

edges:
  bot -write-> segments-stream
  bot -write-> tc-mutable
  meeting-api -read-> segments-stream
  meeting-api -write-> tc-mutable
  meeting-api -write-> segments-table
  meeting-api -write-> tc-stream
  agent-api -read-> tc-stream  # the transcription watcher's only action feed (a segment registers the meeting, session_end ends it), and the live view agent-api relays (/api/meeting/stream); the collector writes it only for entries it admitted (transcript.v1 FeedEntry)
  gateway -read-> tc-mutable
  terminal -read-> tc-stream
  terminal -read-> out-stream
  bot -write-> recording-blob
  bot -read-> userdata-blob  # restore the stored session before launch, with the bots' read-only key pair (BOT_S3_*: Get + List on the userdata prefix and nothing else); the bot never writes the store — its rotated session goes back through meeting-api (bot-session-writeback)
  bot -req-> meeting-api  # an authenticated bot's rotated browser session, on clean teardown: session-profile.v1's route, PUT /internal/browser-session/{session_uid}, at the URL meeting-api names in the bot's invocation (invocation.v1 sessionWritebackUrl, sent only in authenticated mode; the bot derives none), with Authorization: Bearer <MeetingToken> (the invocation.v1 session token) admitted for exactly that session_uid, and only from the live authenticated bot — the newest session spawned on the deployment's identity, of the token's meeting, live or ended under 600 s; anything else is refused (401/403). Carrier: a session-profile.v1 WritebackBody {files: [{path, data (base64)}]} whose every path is one the contract's SessionProfile names, size-bounded per file and in total; remote-browser and meeting-api each read a verbatim copy of the contract
  meeting-api -write-> userdata-blob  # stores an admitted session write-back with meeting-api's own storage credentials (S3_*, else MINIO_*) at BOT_S3_ENDPOINT / BOT_S3_BUCKET under BOT_USERDATA_S3_PATH; the bots' key pair stays read-only
  remote-browser -write-> userdata-blob  # provisioning login uploads the confirmed signed-in session
  gateway -read-> recording-blob
  bot -call-> transcription  # audio -> first-party STT via TRANSCRIPTION_SERVICE_URL
  bot -read-> bot-commands  # SUBSCRIBE acts.v1 commands
  meeting-api -write-> bm-status  # PUBLISH status
  meeting-api -write-> u-meetings  # PUBLISH per-user status
  meeting-api -write-> bot-commands  # PUBLISH leave/reconfigure/chat_send
  meeting-api -write-> recording-blob  # S3 PUT stitched master
  meeting-api -write-> postgres
  meeting-api -write-> object-store
  meeting-api -write-> livekit  # creates a call's room with its transcription notice (Twirp room service)
  agent-api -read-> livekit  # reads a room's metadata to bind a call to its session
  bot -read-> livekit  # the call listener: joins hidden and subscribe-only, and transcribes every audio track each participant publishes, on that participant's channel
  terminal -write-> livekit  # the terminal's page in a person's browser joins a call itself, not the terminal server (signalling over WebSocket; media over the UDP range)
  agent-api -write-> object-store
  admin-api -write-> object-store
  agent-api -write-> postgres
  meeting-api -req-> runtime  # meeting-api drives the kernel with Authorization: Bearer RUNTIME_API_TOKEN (runtime.v1 CallerCredential): POST /workloads to spawn a bot, GET /workloads/{id} to read it and DELETE /workloads/{id} to tear it down; its RuntimeEvents come back signed on rt-ma
  meeting-api -req-> admin-api  # GET /internal/calendar-configs discovers secret-gated calendar connections for sync and disconnect cleanup
  meeting-api -req-> admin-api  # GET /internal/users/{id}/email (X-Internal-Secret) answers {id, email} for a reader of a shared meeting, asked only on the OWNER's access view (GET /meetings/{id}/access) for readers who redeemed before meeting-api kept a roster; meeting-api writes the address into that meeting's own data.share_viewers (one writer) and a failed lookup leaves the reader listed by id (collector/reader_directory.py)
  meeting-api -req-> admin-api  # GET /internal/users/{id}/memberships (X-Internal-Secret), read once per calendar sync run: a new occurrence of a calendar series inherits the series' workspace only while its owner still holds contributor or owner there (R1801-3); an unanswered lookup is 'may write nowhere' and the occurrence is created private (calendar_sync/runner.py, collector/reader_directory.py)
  meeting-api -req-> service-authority  # optional signed service-authority.v1 admit/continue decision; unset is explicit OSS allow-all, configured failure is closed
  meeting-api -req-> system-webhook  # optional signed terminal webhook.v1 delivery to a boot-frozen operator destination over HTTPS (plain HTTP only to a private target the operator opts into); customer webhook SSRF policy remains separate
  agent-api -read-> segments-stream  # the transcription watcher's group agent_copilot: XREADGROUP + XACK each raw entry as a hint only, naming which verified per-meeting feed (tc-stream) to read; it acts on nothing it reads here
  agent-api -req-> runtime  # agent-api drives the kernel with Authorization: Bearer RUNTIME_API_TOKEN (runtime.v1 CallerCredential): POST /workloads, GET /workloads and GET /workloads/{id} to spawn and track agent-worker workloads, and POST /schedule, GET /schedule and DELETE /schedule/{job_id} for routine jobs (schedule.v1)
  agent-api -read-> out-stream  # SSE relay (/api/chat, /api/meeting/stream)
  agent-worker -read-> tc-stream  # copilot tails transcript
  agent-worker -write-> out-stream  # XADD cards/notes/deltas
  agent-worker -read-> unit-in  # chat path XREADs interactive input
  mcp -req-> gateway  # every MCP tool forwards the caller's own bearer (an API key or a worker's delegation token) to the public REST surface; for a worker it also sends back the identity the gateway signed onto the /mcp request (X-Vexa-Internal-Mcp-Identity, gateway-identity.v1 re-entry), the only way a delegation token reaches a REST route
  gateway -req-> meeting-api  # proxy /bots /transcripts /meetings /recordings and per-calendar sync — and, on the service door, the flows tier's allow-listed meetings calls with the subject's identity signed here (scopes bot+tx)
  gateway -req-> agent-api  # proxy /agent/* with the resolved identity signed (gateway-identity.v1) — and, on the service door, the flows tier's allow-listed agent-api calls over the internal tier
  gateway -req-> mcp  # proxy /mcp — the ONE assembled MCP server for every bearer, a person's key or a worker's delegation token; POST buffered, GET relayed unbuffered (SSE stream)
  gateway -req-> admin-api  # POST /internal/validate (authz oracle) plus user calendar connection CRUD — and, on the service door, the flows tier's allow-listed identity calls (address and id, ensure a person, settings, is-admin, a room's principal, a bearer's validation, the subject's identity the door signs for meetings calls)
  gateway -read-> bm-status  # WS fan-out
  gateway -read-> u-meetings  # WS auto-subscribe
  gateway -read-> va-chat  # WS fan-out
  meeting-api -write-> va-chat  # PUBLISH a ws.v1 chat_message per stored chat segment (collector/ingest.py)
  admin-api -write-> identity-db
  admin-api -write-> postgres
  terminal -req-> gateway  # every REST call a browser makes, via gateway (the terminal's server also calls admin-api and agent-api directly: term-admin-internal, term-agent-internal)
  terminal -req-> gateway  # live WS via gateway
  terminal -req-> admin-api  # the terminal's server, with the internal secret: sign-in admission, the admin claim, the claim-code check, instance state, the one-use redeem of an emailed sign-in link and the binding of an OAuth sign-in to its provider subject (signin.v1), plus /internal/validate and the settings it edits for the admin
  terminal -req-> agent-api  # the terminal's server, with the internal secret: POST /internal/scaffolds (a sign-in's arrival) and GET /internal/has-history
  terminal -req-> flows-api  # the terminal's server, through the gateway's service door (/to-flows: the door checks its internal secret and forwards with the flows key): POST /mail/sign-in hands an emailed sign-in link to the flows mailer (VEXA_FLOWS_API_URL); on any failure the terminal sends it over its own SMTP
  dashboard -req-> gateway  # dashboard → gateway REST (hosted-compat aliases; the hosted-proven wiring)
  dashboard -req-> gateway  # dashboard → gateway /ws (live transcript view)
  slim -req-> gateway  # Python client; REST via gateway
  extension -req-> gateway  # browser extension client; live WS via gateway
  flows-worker -write-> flows-rows
  flows-api -write-> flows-rows  # the second writer, recorded because it is real: POST /events admits a fact in the API process (flows_integrations/flows_api.py → flows.admit → INSERT INTO reaction) and the registry writes flow_version there too. The chart carried only flows-worker, so the one shared carrier in this domain read as single-writer
  flows-api -read-> flows-rows
  flows-worker -req-> gateway  # every call a flow step makes into another domain goes through the gateway's SERVICE DOOR (core/gateway/services/gateway/src/gateway/service_door.py, port 8002, never published) with the flows tier's one credential VEXA_FLOWS_SERVICE_KEY; flows-api's identity, timeline and mail-admin calls take the same door. The door forwards only its fixed allow-list (FLOWS_OUTBOUND): agent-api, admin-api and crm-api over the internal tier with the named subject, meeting-api with the subject's identity signed by the gateway (scopes bot+tx); anything else is a 403 at the door. flows holds no INTERNAL_API_SECRET and no admin key, and a domain never knows flows exists
  agent-api -req-> credentials-broker  # agent-api to the credential broker, signed role assertions (credential-broker.v1): role agent for the Connections routes (request, list, read, draft, call; no consent, no stored credential returned) and role git for the Git credential store (the person's own Git credentials and their shared workspaces' deploy keys); every call of either role carries the gateway's X-Vexa-Identity for the person, unchanged, and the broker acts only for the subject it names
  terminal -req-> credentials-broker  # the terminal's server to the credential broker, role human for the identity-validated person (credential-broker.v1): consent, credential save, disconnect, delete
  credentials-broker -req-> credentials-vault
  credentials-broker -write-> credentials-store
  agent-worker -req-> gateway  # the worker's toolbelt: /mcp and /agent/friction with its per-dispatch delegation token, which identity resolves as the person it acts for. With the model proxy configured, the harness's own /mcp traffic goes through the proxy instead (aw-mp) and the token stays in the worker process, for its in-process calls
  mcp -req-> agent-api  # boot assembly: GET /.well-known/mcp-tools.json + /openapi.json — the agent domain's tools join the one MCP surface
  agent-api -req-> meeting-api  # agent-api reads meetings as the caller, X-User-Id (and X-User-Workspaces) over the internal tier (X-Internal-Secret): GET /meetings/{id} (the meeting access lookup behind every meeting-scoped agent route), GET /transcripts/by-id/{id} (a transcript the caller may read), GET /meetings?… (the schedule digest's three bounded queries, also read by the desktop to place a workspace's last and live call), POST /meetings/{id}/annotate (the minted meeting's recorder). meeting-api decides access; agent-api holds no meetings data
  agent-api -req-> admin-api  # agent-api asks identity about a person over the internal tier (X-Internal-Secret): GET /internal/users/by-email/{email} (falling back to GET /admin/users/email/{email} with X-Admin-API-Key) to resolve a share's invitee; POST/DELETE/GET /internal/users/{id}/memberships[/{ws}] (the membership index mirror); GET /internal/users/{id}/model-config; GET /internal/users/{id}/is-admin (the _global tier's writer); GET /internal/users/{id}/bot-context (admin overview); GET/PUT /internal/users/{id}/settings (the person's clock and timezone, and their presence switch), GET /internal/presence?ids=… (many people's presence choice at once, for the desktop and the people strip); GET /internal/users/{id}/photo (a person's photo bytes, asked only after agent-api decided the viewer shares a workspace or chat with them); GET /internal/users/{id}/org (the viewer's org for every workspace access check, cached)
  agent-worker -req-> flows-api  # the worker's temporal block: GET /timeline?format=preamble with the read-only VEXA_FLOWS_TIMELINE_KEY that dispatch stamps only when the deployment minted one (never the operator key); granted by the workload network fences (compose workers network, the Helm workload egress policy)
  bot -req-> meeting-api  # the bot's calls back: every lifecycle.v1 event to POST /bots/internal/callback/lifecycle and every recording chunk to POST /internal/recordings/upload, each with Authorization: Bearer <MeetingToken> (invocation.v1 token), bound to the bot's own session; meeting-api refuses another session's token with 401. An authenticated bot's session write-back is the third call, on its own edge (bot-session-writeback)
  runtime -req-> agent-api  # a routine's due schedule.v1 job: POST /invocations with the unit.v1 dispatch agent-api compiled and signed (X-Vexa-Dispatch-Signature, HMAC keyed from INTERNAL_API_SECRET); the runtime holds the job opaquely and cannot re-point it at another person or trigger
  runtime -req-> meeting-api  # every RuntimeEvent for a bot workload to its callbackUrl, POST /runtime/callback, signed X-Runtime-Signature (runtime.v1 CallbackSignature, keyed from RUNTIME_API_TOKEN); meeting-api refuses an unsigned or forged callback with 401
  agent-api -write-> imports-status  # repository import status (workspace_import.py)
  agent-api -write-> onboarding-research-state  # onboarding research checkpoints (onboarding_research.py)
  agent-api -write-> rail-order  # SET/GET the chat rail order
  agent-api -write-> rail-marks  # HSET/HDEL/HGETALL a person's rail pins and hides
  admin-api -write-> signin-link-redeemed  # SET NX with the link's own expiry (at most an hour) when a terminal redeems an emailed sign-in link; a write that cannot be made refuses the sign-in
  claude-plugin -req-> gateway  # the plugin's HTTP MCP server entry: Claude Code calls the gateway's /mcp with the person's Vexa API key; the plugin itself runs no code
  agent-api -write-> unit-in  # XADD the person's next message to a warm unit, signed with the unit's key
  agent-api -write-> redis-acl-users  # ACL SETUSER/DELUSER a worker's own user per dispatch; restore after a Redis restart
  meeting-api -write-> redis-acl-users  # ACL SETUSER/DELUSER a bot's own user per session; restore after a Redis restart
  agent-api -write-> acl-units-index  # HSET/HDEL the worker users it defined
  agent-api -write-> delegation-revoked  # SET revoked:<jti> for the token's remaining life when the runtime no longer runs its unit
  agent-api -write-> delegation-live  # SET live:<jti> for the token's life when it is recorded; DEL it when the token is revoked
  admin-api -read-> delegation-live  # EXISTS live:<jti> for every verified vxd_ bearer; a token without it is refused (401), a store it cannot read refuses the token (503)
  admin-api -read-> delegation-revoked  # EXISTS revoked:<jti> for every verified vxd_ bearer; a store it cannot read refuses the token (503), API keys never read it
  agent-api -write-> delegation-records  # HSET/SADD a token's jti against its unit before the spawn and at each refresh; HDEL/SREM as tokens are revoked or expire
  agent-api -write-> delegation-current  # SET the unit's current token at dispatch and at each refresh, and GET it to re-mint
  agent-api -write-> unit-delegation  # SET the unit's current token for its worker at dispatch and at each half-life refresh
  agent-worker -read-> unit-delegation  # GET before every turn, write-back and job; read-only to its Redis user
  agent-api -write-> unit-fault  # SET the typed fault a refused spawn ended in; GET it for the SSE relay and the pending list; DEL on the next spawn
  meeting-api -write-> acl-bots-index  # HSET/HDEL the bot users it defined
  agent-api -write-> routine-state  # routine approvals and the re-signing marker
  agent-api -req-> flows-api  # the publish edge: POST /events (desk.unscaffolded, claim.proposed) and POST /friction with the operator key (X-Flows-Operator-Key); GET /flows/pages to land the pages of authored flows in _global/flows/
  agent-api -req-> gateway  # the transcription watcher, with VEXA_BOT_API_KEY: GET /meetings to resolve a meeting row to its native id, and POST /meetings/{platform}/{native}/docs to link the meeting's own page on session end
  mcp -req-> flows-api  # boot assembly: GET /.well-known/mcp-tools.json + /openapi.json — the flows domain's tools join the one MCP surface; its operator-keyed tools need the MCP to hold the key its admin_auth names
  mcp -req-> meeting-api  # boot assembly: GET /.well-known/mcp-tools.json + /openapi.json on the meetings domain's door; a 404 is a deployed domain that publishes no manifest, and contributes no tools
  mcp -req-> admin-api  # boot assembly: GET /.well-known/mcp-tools.json + /openapi.json on identity's door, always configured; a 404 is a domain that publishes no manifest, and contributes no tools
  meeting-api -write-> meeting-bundle-file  # GET /meetings/{meeting_id}/export — the owner's whole meeting as a meeting-bundle.v1 zip
  meeting-api -read-> meeting-bundle-file  # POST /meetings/import — validate a meeting-bundle.v1 zip (manifest, every hash, paths, sizes) and land it as a new meeting the importer owns
  agent-api -write-> meeting-bundle-parts  # GET /api/meeting/bundle-parts — the owner's bound workspace tree (never its roster or policy) and the meeting's page
  meeting-api -read-> meeting-bundle-parts  # POST /meetings/{meeting_id}/export — the parts placed into the bundle
  agent-api -read-> meeting-bundle-file  # POST /api/meeting/bundle-restore — the bundle's workspace part as a new private workspace of the importer and its notes page on their desk, re-bound to the imported meeting
  crm-api -write-> postgres
  crm-api -req-> admin-api  # POST /internal/validate: who the caller is, their live workspace memberships narrowed to a worker's ceiling, and whether the turn is shared; GET /internal/users/{id}/org (only with personal_crm on): whether the caller is an individual served from their own org of one
  admin-api -req-> crm-api  # account delete: POST /internal/orgs/p-{id}/erase (internal secret) erases an individual account's personal CRM before identity removes the account (capability personal_crm_erase, CRM_API_URL)
  mcp -req-> crm-api  # boot assembly and tool calls: GET /.well-known/mcp-tools.json + /openapi.json, then the crm_*, desktop_* and card_* tools with the caller's own credential; present only where CRM_API_URL is set
  gateway -req-> crm-api  # the edge's /crm/* forward (routes.v1 `forward`): the record page's read, history and review, the desktop's desk/read, desk/arrange, desk/proposal and desk/undo (served while `desktop` is on), and the cards' card/list, card/read, card/create, card/update, card/members and card/membership (served while `desktop_cards` is on), with the caller's own key, present only where CRM_API_URL is set — and, on the service door, the flows tier's POST /internal/references and /internal/cards/offer over the internal tier
  gateway -req-> flows-api  # the service door's inbound leg (/to-flows): the internal-tier callers of flows-api (the signals service's POST /events/signal, the terminal's POST /mail/sign-in) present X-Internal-Secret to the door, which forwards the allow-listed calls (FLOWS_INBOUND) with X-Vexa-Service-Key, so flows never receives the internal secret
  crm-api -req-> agent-api  # GET /api/workspace/file?path=POLICIES.md&slug=_global over the internal tier (subject crm): the company's desk limits, narrow-only, cached 30 s; optional — CRM_AGENT_API_URL unset or unreachable means the defaults
  signals-service -read-> redis
  signals-service -req-> agent-api  # GET /internal/meetings/{row}/listen?subject=<owner> and GET /internal/chats/{session}/listen?subject=<owner> with the internal secret: the listening brief (the agent's rules and model tier included) and the company's signal policy in one answer, cached 30 s; agent-api reads the source as that subject
  signals-service -req-> flows-api  # POST /events/signal through the gateway's service door (/to-flows: the door checks X-Internal-Secret and forwards with the flows key, so flows never receives the secret; X-Vexa-Service: signals): one event.v1 meeting.signal per wake, data = the buffer; the company's _global flow decides what a wake does. A publish edge: no flows, no wakes
  crm-api -req-> flows-api  # the crm outbox (python -m crm.outbox --watch, the crm-outbox process of the crm image): POST /events with the CRM's publish key (X-Flows-Publisher-Key, VEXA_FLOWS_CRM_PUBLISH_KEY: the CRM's own facts, no other flows route), one crm.record.changed per revision with its event.v2 envelope signed with the CRM's own Ed25519 key (VEXA_FACT_SEAL_KEY_FILE; flows verifies under its public half); marked delivered only on a 202. A publish edge: no flows, rows stay pending
  signals-service -req-> credentials-broker
  signals-service -call-> signal-model  # one call per confirmed line: the line, up to 24 lines before it and the brief's questions; answers a 0..1 score per label, parsed strictly
  agent-worker -call-> model-proxy  # every model request a turn's harness makes (/llm/*, its turn's token as the bearer — never a provider key) and the harness's MCP toolbelt (/mcp, the same token; the proxy presents the delegation bearer); the worker revokes the token when the last run holding it ends
  model-proxy -call-> gateway  # a harness's MCP request relayed to the gateway's /mcp with the unit's current delegation token (agent-api's record, else the one sealed at spawn)
  agent-api -write-> model-routes  # SET a cold spawn's sealed route and the unit's pointer to it; GET the pointer for a message to a running worker
  agent-api -write-> model-grants  # SET one turn's grant per delivered message (the spawn's entrypoint, every unit-in entry)
  model-proxy -read-> model-routes  # GET and open (AES-GCM) the route a live grant names
  model-proxy -write-> model-grants  # GET the grant a request's token names; INCR its request count; DELETE both on revocation
  model-proxy -write-> llm-unreported-spend  # INCRBYFLOAT each relayed call's priced usage onto the turn's unreported figure; read every figure of the payer and the deployment, with the spend ledger, before each model request
  agent-api -write-> llm-unreported-spend  # the usage meter takes each amount a worker reports off the turn's figure and clears it at turn-complete, recording what is left when the turn reported nothing
  model-proxy -read-> delegation-current  # GET the unit's current delegation token to present on a relayed MCP request
  model-proxy -call-> model-provider  # an agent turn's model request, forwarded by the model proxy with the turn route's credential: the prompt with its meeting grounding (transcript excerpts) and the workspace context the turn reads
  agent-api -call-> model-provider  # the one model call a home composition may make (core/agent/control_plane/home_model.py): the cheapest catalog model orders the cards it is handed, at most once per composition
  meeting-api -call-> customer-webhook  # signed webhook.v1 delivery of meeting lifecycle, recording and transcription-ready events to the customer's registered URL
  bot, agent-worker deployed-in runtime
  gateway, meeting-api, agent-api, model-proxy, admin-api, runtime, redis, postgres, object-store, transcription deployed-in deploy
  flows-api, flows-worker deployed-in deploy

flows:
  live-transcript-flow: bot-writes-segments-stream -> collector-reads-segments -> collector-writes-tc -> aw-tcnative
  dispatch-flow: aa-runtime -> workers-deployed -> aw-unitout -> aa-unitout
