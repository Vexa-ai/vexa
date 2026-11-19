- **Calls in Minutes (#1783, ADR-0048 decisions 6 and 7).** A chat with other people in it has a
  **Call** button. The call runs on the deployment's own LiveKit server, an optional module (Compose
  profile `calls`, Helm `livekit.enabled`; not in Lite). The call is a meeting with platform
  `vexa_call`: a listener without a browser transcribes each participant's own audio, so attribution
  comes from the track. A participant reads the *this call is transcribed* notice and presses
  **Join** before the dock connects or opens the microphone, and the transcriber is listed among the
  people in the call. The chat's agent follows the live transcript; **Private** opens your own chat
  about the call, and **Share to the room** posts one line into the shared chat. After the call, the
  post-meeting run (`post_meeting@5`) proposes the report and its commitments to the call's owner,
  who accepts, edits or rejects each one; nothing lands in a workspace before that, unless the new
  policy `post_meeting_auto_accept` (default `off`) is on. A call whose meeting does not start (bot
  limit, service authority, listener spawn failure) removes its room at once. New routes:
  `POST /bots {"platform":"vexa_call"}`, and `/agent/sessions/{s}/call`, `…/call/token`,
  `…/messages` and `…/proposals`. New keys, set on Compose (`.env`) and Helm: `LIVEKIT_URL`,
  `LIVEKIT_INTERNAL_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, and `CALL_MAX_DURATION_SEC`,
  `CALL_EMPTY_GRACE_SEC`, `CALL_FIRST_JOIN_TIMEOUT_SEC`, `CALL_MAX_PARTICIPANTS`,
  `CALL_TRANSCRIPTION_SWITCHABLE` (Helm `meetingApi.calls.*`). For networks that block UDP, LiveKit's
  TURN over TLS can be turned on with an operator certificate (Compose `LIVEKIT_TURN_TLS_PORT`,
  `LIVEKIT_TURN_CERT_FILE`, `LIVEKIT_TURN_KEY_FILE`; Helm `livekit.turn.tlsPort`,
  `livekit.turn.tlsSecretName`); it is off by default. On Helm the LiveKit server runs as uid 10001
  with every capability dropped, and its signalling port binds the pod's own IP
  (`livekit.bindAddresses` to override); with `hostNetwork`, the bot egress the docs describe is one
  `/32` rule for the LiveKit node and its ports. On Compose the runtime no longer receives
  `LIVEKIT_API_SECRET`. See [Calls](/minutes/calls).
