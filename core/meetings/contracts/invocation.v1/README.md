# invocation.v1 — the meeting-bot's constructor

The bot's input, delivered as **one JSON env var `VEXA_BOT_CONFIG`** (ADR-0002) and validated at boot
(fail-fast). It names the meeting to join + the egress endpoints (`redisUrl` for the acts.v1 bus +
transcript egress, `meetingApiCallbackUrl` for the lifecycle.v1 sink) + transcription/recording/voice
flags + S3/auth.

## Secrets
A *config* contract legitimately carries the secrets the bot needs — `token` · `redisUrl` ·
`transcriptionServiceToken` · `s3AccessKey` · `s3SecretKey` are marked **SECRET** and appear as
**placeholders** in goldens (never real values, P14). The P15 ideal — env carries a secret-store
*reference* the bot resolves at boot — is deferred; for now the raw fields are faithful to today's wire.

**`redisUrl` carries a Redis credential.** By default (per-workload ACL) it is the URL of a Redis user
meeting-api defines for this session alone, granted exactly: append to `transcription_segments`,
publish on `tc:meeting:{meeting_id}:mutable`, subscribe to `bot_commands:meeting:{meeting_id}`, and the
commands `xadd publish subscribe unsubscribe ping quit`. The user is removed when the session ends.
**With `REDIS_WORKLOAD_ACL=shared`**, a deployment's choice for a Redis that cannot define users,
`redisUrl` carries meeting-api's own service connection instead: every bot then holds a credential
that reaches everything meeting-api's does.

## The bot's credential: `token`
`token` is the session's **MeetingToken**: an HS256 token meeting-api mints for each spawn and binds
to the session (`session_uid` = `connectionId`). Besides the Redis user in `redisUrl`, it is the only
credential a bot holds, and it has four uses, three of them HTTP doors:

| Use | Where | How |
|---|---|---|
| every lifecycle.v1 event | `meetingApiCallbackUrl` | `Authorization: Bearer <token>` |
| every recording upload | `recordingUploadUrl` | `Authorization: Bearer <token>` |
| an authenticated bot's session write-back (clean teardown) | `sessionWritebackUrl` | `Authorization: Bearer <token>`, body session-profile.v1 `WritebackBody` |
| every transcript entry | the `transcription_segments` stream | `auth` + `sig` beside the payload (transcript.v1 `StreamEntry`); the token itself never enters Redis |

meeting-api refuses the token for any other session. **`internalSecret` is deprecated and must not be
set**: no producer sends it and the bot ignores it. It remains in the schema only so a bot can still
parse an invocation from an older meeting-api.

**Minimum bot version: v0.13.2.** meeting-api from v0.13.2 requires the bearer, and a bot from before
it sends none, so every callback and upload of an older bot is refused (401) and its meetings never
leave `requested`/`joining`. Compose's `BROWSER_IMAGE` and the chart's `runtime.browserImage` can pin
the bot apart from the control plane; when either is pinned, move it to v0.13.2 or later in the same
upgrade as meeting-api.

## The authenticated bot: `authenticated` and `sessionWritebackUrl`
In a deployment's authenticated mode, meeting-api sends `authenticated` with the read-only userdata
store (`userdataS3Path`, `s3*`), from which the bot restores the
[session-profile.v1](../session-profile.v1) profile before launch, and `sessionWritebackUrl`, the
meeting-api URL (this session's uid included) the bot PUTs its rotated session to on clean teardown.
The bot reads the URL from here and builds none: an invocation without it gets no write-back.

**Fields added in v0.13.2 that an older bot refuses.** The schema sets `additionalProperties: false`,
so a bot older than v0.13.2 refuses at boot an invocation that carries `transcriptionServiceOwner`
(sent only as `customer`) or `sessionWritebackUrl` (sent only in authenticated mode). Both follow the
minimum bot version above: upgrade bots and meeting-api together.

## Shape
`Invocation` (`$defs`): required `platform · meetingUrl · botName · redisUrl`; everything else optional.
`automaticLeave` defaults the three timeouts. No tenancy fields (deferred, ADR-0003).

Goldens (`Invocation.<case>.json`) validated by `gate:schema`.
