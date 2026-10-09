# invocation.v1 — the meeting-bot's constructor

The bot's input, delivered as **one JSON env var `VEXA_BOT_CONFIG`** (ADR-0002) and validated at boot
(fail-fast). It names the meeting to join + the egress endpoints (`redisUrl` for the acts.v1 bus +
transcript egress, `meetingApiCallbackUrl` for the lifecycle.v1 sink) + transcription/recording/voice
flags + S3/auth.

## Secrets
A *config* contract legitimately carries the secrets the bot needs — `token` ·
`transcriptionServiceToken` · `s3AccessKey` · `s3SecretKey` are marked **SECRET** and appear as
**placeholders** in goldens (never real values, P14). (Contrast `transcript.v1`, a *data* contract,
which carries no auth at all.) The P15 ideal — env carries a secret-store *reference* the bot resolves
at boot — is deferred; for now the raw fields are faithful to today's wire.

## The bot's credential: `token`
`token` is the session's **MeetingToken**: an HS256 token meeting-api mints for each spawn and binds
to the session (`session_uid` = `connectionId`). It is the only credential a bot holds, and it is the
**transport bearer** on both of the bot's calls back into meeting-api:

| Call | To | Header |
|---|---|---|
| every lifecycle.v1 event | `meetingApiCallbackUrl` | `Authorization: Bearer <token>` |
| every recording upload | `recordingUploadUrl` | `Authorization: Bearer <token>` |

meeting-api refuses the token for any other session. **`internalSecret` is deprecated and must not be
set**: no producer sends it and the bot ignores it. It remains in the schema only so a bot can still
parse an invocation from an older meeting-api.

**Minimum bot version: v0.13.2.** meeting-api from v0.13.2 requires the bearer, and a bot from before
it sends none, so every callback and upload of an older bot is refused (401) and its meetings never
leave `requested`/`joining`. Compose's `BROWSER_IMAGE` and the chart's `runtime.browserImage` can pin
the bot apart from the control plane; when either is pinned, move it to v0.13.2 or later in the same
upgrade as meeting-api.

## Shape
`Invocation` (`$defs`): required `platform · meetingUrl · botName · redisUrl`; everything else optional.
`automaticLeave` defaults the three timeouts. No tenancy fields (deferred, ADR-0003).

Goldens (`Invocation.<case>.json`) validated by `gate:schema`.
