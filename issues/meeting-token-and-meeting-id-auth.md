---
title: Replace user-token propagation and session UIDs with meeting-id + MeetingToken (HS256)
type: Architecture Decision
status: Approved
priority: High
components: [bot-manager, vexa-bot, WhisperLive, transcription-collector]
created: 2025-10-11
related: [bot-reconfiguration-identity-mismatch.md]
owners: [dgrankin]
version: 1.0
---

### Replace user-token propagation and session UIDs with meeting-id + MeetingToken (HS256)

**Issue Type**: Architecture Decision & Implementation Plan

**Status**: Proposed → Approved

**Related**: `bot-reconfiguration-identity-mismatch.md`

---

### Executive Summary

- **Problem**:
  - **Raw user token propagation**: user API tokens flow across services (vexa-bot → WhisperLive → transcription-collector).
  - **High DB load**: transcription-collector validates every message by hitting the database with provided tokens.
  - **Identity mismatch**: control-plane addresses bots via session UIDs while data-plane uses ephemeral WS UIDs. See `bot-reconfiguration-identity-mismatch.md`.
- **Decision**:
  - Use stable **meeting_id** (DB PK) as the single identity for control/data planes.
  - Replace user-token propagation with a **MeetingToken** (JWT HS256) signed by a single shared secret (`ADMIN_TOKEN` from `vexa/.env`).
  - Verify MeetingToken cryptographically at transcription-collector; eliminate per-message DB lookups.

---

### Problem

- **Passing user token unhashed through the system**:
  - Increases risk surface; tokens could leak in logs, payloads, or via intermediary compromise.

- **Per-message DB validation**:
  - transcription-collector currently resolves `token → user → meeting` for each stream message, creating unnecessary load and latency.

- **Session UID addressing** (from related issue):
  - Session UIDs are ephemeral and unsuitable for addressing; on reconnects, bots appear as new sessions.
  - Command routing requires Redis/DB lookups to resolve session UIDs, adding fragility and overhead.

---

### Decision

- **Control/Data identity**: meeting-centric. Use `meeting_id` everywhere (commands and data).
- **Authorization**: MeetingToken = JWT (HS256) minted by bot-manager using `ADMIN_TOKEN`.
- **Collector validation**: verify signature + claims; no DB on the hot path.

---

### Architecture / MeetingToken Specification

- **Format**: JWT (header.payload.signature), signed with HS256 using `ADMIN_TOKEN`.
- **Header**: `{ "alg": "HS256", "typ": "JWT" }`
- **Claims (payload)**:
  - `meeting_id`: integer (DB primary key)
  - `user_id`: integer (owner of meeting)
  - `platform`: string (e.g., `google_meet`, `teams`)
  - `native_meeting_id`: string (platform-native meeting id)
  - `scope`: `"transcribe:write"`
  - `iss`: `"bot-manager"`
  - `aud`: `"transcription-collector"`
  - `iat`: issued-at (epoch seconds)
  - `exp`: expiry (short TTL; e.g., 15–60 min)
  - `jti`: unique token id (for replay protection)

Example claims:

```json
{
  "meeting_id": 12345,
  "user_id": 789,
  "platform": "google_meet",
  "native_meeting_id": "abc-def",
  "scope": "transcribe:write",
  "iss": "bot-manager",
  "aud": "transcription-collector",
  "iat": 1731246400,
  "exp": 1731250000,
  "jti": "9f4b2a2e-..."
}
```

---

### Architecture / End-to-End Flow (meeting-id centric)

- **Issuance (bot-manager)**
  - Validate `X-API-Key` → resolve `user_id`.
  - Create/find meeting → get `meeting_id`.
  - Mint MeetingToken (HS256) with claims above using `ADMIN_TOKEN`.
  - Launch bot with `meeting_id`, `MeetingToken`, and metadata (platform, native_meeting_id, language, task).

- **Transport**
  - vexa-bot → WhisperLive (initial WS config): send `meeting_id` + `meeting_token`.
  - WhisperLive treats token as opaque; forwards to Redis:
    - session_start: include token (required to seed validation/cache).
    - transcription/speaker events: either include token each message (stateless) or send only `meeting_id` with per-message MAC derived from the token (stateful option, see below).

- **Verification (transcription-collector)**
  - Verify JWT signature with `ADMIN_TOKEN`; enforce claims: `exp/iat/nbf`, `iss`, `aud`, `scope`, required IDs.
  - On first valid message for a meeting:
    - Cache `meeting_id → { user_id, platform, native_meeting_id, token_exp }` in memory (TTL = remaining token lifetime).
    - Optionally cache `jti` to prevent replay.
  - Subsequent messages:
    - Stateless: verify token each time (crypto-only, still DB-free).
    - Stateful: verify per-message MAC using a per-meeting key derived from the JWT (prevents Redis injection without resending the token).

---

### Control-Plane Commands (meeting-id addressing)

- **Channel**: `bot_commands:meeting:{meeting_id}`
- **Bot subscription**: subscribe once using its `meeting_id` from `BOT_CONFIG`.
- **Bot-manager publishing**: publish reconfigure/leave directly to the meeting-based channel; include `meeting_id` in payload for bot-side validation.
- This unifies identity across control and data planes and removes any dependency on session UIDs. See `bot-reconfiguration-identity-mismatch.md`.

---

### Security Considerations

- **No raw user tokens**: only bot-manager ever sees user API keys; they never traverse services.
- **Short TTL**: minimize blast radius; re-mint if long-running.
- **Replay protection**: use `jti` cache; expire at `exp`.
- **Redis injection resistance**:
  - Stateless mode: signature verification per message (includes claims).
  - Stateful mode: derive a per-meeting key (e.g., HKDF(MeetingToken)) and attach `mac = HMAC(derived_key, message_payload)` for each message.
- **Key management**: single shared secret `ADMIN_TOKEN` in `vexa/.env` available to bot-manager and transcription-collector only.

---

### Implementation (Mandatory; no flags)

- **Bot-manager**
  - Mint MeetingToken (HS256) using `ADMIN_TOKEN`.
  - Pass `meeting_id` + `meeting_token` to bot; stop passing user API token.
  - Publish commands ONLY to `bot_commands:meeting:{meeting_id}`.
  - Remove `(platform,native_meeting_id) → current_uid` Redis mapping.

- **vexa-bot**
  - Send `meeting_id` + `meeting_token` in WhisperLive initial config.
  - Subscribe to `bot_commands:meeting:{meeting_id}`.
  - Validate `meeting_id` in command payloads before acting.
  - Fail fast if `meeting_id` missing in config.

- **WhisperLive**
  - Treat token as opaque; forward it in `session_start` (and per message if stateless).
  - Require `uid`, `meeting_id`, `token`, `platform`, `meeting_url` on initial config; else respond ERROR and close.
  - Include `meeting_id` on all messages.

- **transcription-collector**
  - Verify JWT (HS256) with `ADMIN_TOKEN`.
  - Enforce claims and required fields; cache by `meeting_id`.
  - Eliminate per-message DB lookups; optional per-message MAC later.

---

### Testing (Fail-Fast enforced)

- **Unit**: token mint/verify; claim validation; meeting-id channel generation; command validation in bot.
- **Integration**: bot launch → WS → Redis → collector store; reconfigure and leave using meeting-id channels.
- **Resilience**: WS reconnects, bot restarts; collector restart with stateful cache warm-up.
- **Security**: replay attempts, tampered tokens, Redis injection attempts (with and without per-message MAC).

---

### Acceptance Criteria (No Backward Compatibility)

- **No raw user tokens** leave bot-manager.
- **Collector** processes valid messages without per-message DB lookups.
- **Commands** addressed to `bot_commands:meeting:{meeting_id}` work (reconfigure/leave).
- **Docs** updated; session UID mapping removed; meeting-only channels mandatory; missing fields cause immediate errors.

---

### Notes

- We intentionally use HS256 with `ADMIN_TOKEN` (single shared secret already present in `vexa/.env`) to avoid introducing a new key pair. If future isolation is needed, we can migrate to RS256 with JWKS and per-service public keys.


