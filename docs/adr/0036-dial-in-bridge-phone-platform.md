# ADR 0036 — The dial-in bridge: a `phone` platform for rooms with no web meeting

**Status:** proposed · 2026-09-17 · spike for
[#1688](https://github.com/Vexa-ai/vexa/issues/1688) · business owner
[`DmitriyG228/biz#473`](https://github.com/DmitriyG228/biz/issues/473) (Vexa Rooms)

## Context

Vexa has exactly one way into a conversation: drive a browser into a web meeting. `@vexa/join`
takes a Playwright `Page` and joins Google Meet, Teams, Zoom or Jitsi
(`core/meetings/modules/join/src/index.ts`); page-side capture pumps PCM across the Playwright
boundary into the pipeline (`core/meetings/services/bot/src/capture-bridge.ts`).

**A room with a speakerphone and no video call is therefore unreachable.** People sit down, the
Poly or Yealink on the table dials a bridge number, and the conversation happens entirely on the
PSTN. There is no URL to join, no page to drive, and nothing for a bot to be admitted to. This is
the *Local* case of Vexa Rooms, and it is the one case the current architecture cannot express at
all — not badly, not partially: there is no platform value for it.

The question a spike can settle cheaply, without a trunk or a provider account, is narrow:

> **Does the transcription engine accept a non-browser audio source as it stands today?**

Verified against `origin/main` at `dba990b41` while settling this — code read, then run:

- The engine's capture entry is `BotPipeline.feedAudio(channel, glowName, pcm, tsMs)` /
  `feedMixedAudio(pcm, tsMs)` (`core/meetings/services/bot/src/pipeline.ts`). **Nothing below that
  seam mentions a browser.** The browser lives entirely above it, in `capture-bridge.ts`, whose own
  header says so: PCM crosses as plain `(speakerIndex, samples)` over `page.exposeFunction`.
- The per-channel lane (`@vexa/gmeet-pipeline`) names a channel from a name supplied **at capture
  time** and does no diarization. A source that knows its own speaker needs no separation.
- `Platform` in `config.ts` is the compile-time mirror of `invocation.v1`'s enum, and
  `parseInvocation` validates against the published schema — so the dispatch contract is a real
  boundary, not a convention.
- `find_meeting_link` (`…/collector/meeting_link.py`) scans ICS free text for `http(s)` URLs only.

The spike answered the question: **yes, unmodified.** A 16 kHz WAV read off disk, pumped through
the new phone adapter, produces `transcript.v1`-valid segments attributed to the room
(`core/meetings/services/bot/src/phone-adapter.test.ts`). The media half of a dial-in bridge is a
**transport** problem, not a pipeline problem.

## Decision

Add `phone` as a platform in the *capture* vocabulary — never in the dispatch contract — and give
it its own adapter that ends at the same pipeline seam the browser bridge ends at.

### 1. `phone` is a capture platform, not a dispatchable one

Every existing platform is **outbound**: a customer calls `POST /bots`, we dispatch a container,
it joins. A dial-in call is **inbound**: the room calls us, and nothing is dispatched at it. So:

- `invocation.v1`'s `Platform` enum stays at four values, and `parseInvocation` keeps refusing
  `phone` from `VEXA_BOT_CONFIG` (pinned as a negative control in `phone-adapter.test.ts`).
- The widening lives in `PipelineInvocation` (`pipeline.ts`) — `Invocation` with a `CapturePlatform`
  — so lane construction can name `phone` while the dispatch contract does not.
- `@vexa/join`'s own `Platform` union **does** carry `phone`, and `joinMeeting` refuses it by name:
  a dial-in call has no page, and a refusal at the dispatch is louder than a join flow failing on
  selectors minutes later (the `#816` lesson, applied before the bug exists).

When inbound dispatch is built, it gets its own contract. It does not borrow this one.

### 2. Identity — the address is not the call

Two facts, deliberately separated:

| | What it is | Who mints it |
|---|---|---|
| **address** | what you dial: `+15551234567`, `room-3@calls.example.org`, `+15551234567:482913` (DID + PIN) | `parsePhoneTarget` (TS) · `parse_phone_url` (Py) — pure, no clock |
| **native_meeting_id** | ONE call on that address: `<address>:<discriminator>` | `phoneNativeMeetingId` — discriminator is the trunk's `Call-ID` in production, the start instant offline |

A DID hosts every call the room ever makes, so **the address alone can never key a meeting** — two
calls colliding on one key would merge two conversations into one transcript. A pasted `tel:` link
therefore names a *room*, exactly as a Jitsi link names a room rather than a conversation. SIP's
`Call-ID` is globally unique by RFC 3261, which is precisely the property needed; the offline
fallback is the compacted UTC start instant.

`sip:` and `sips:` produce the **same** identity. TLS is a trunk concern, never an identity one — a
room that changed id the day its trunk enabled TLS would split its own history in two.

### 3. Attribution — the room is one speaker, and that is final

A conference speakerphone beamforms, echo-cancels, noise-suppresses and **mixes every microphone in
the room before the call leaves it**. By the time packets reach a trunk the separation is gone. No
provider API can un-mix it, because the mixing happened before any API existed to ask.

So the adapter feeds **one channel, named at capture with the room's own label** — the per-channel
lane with a single channel, not the pyannote mix. Running a diarizer over a room mix would invent
divisions the audio does not carry and name them with confidence. We do not do that. The room's
label comes from the DID's registration in the deployment, never from the audio.

Per-person attribution inside a room needs a second device per person (a companion laptop, a phone,
a badge). That is a different product decision, not a later increment of this one.

### 4. Media path

```
room speakerphone ──SIP/RTP──▶ trunk ──▶ [ SIP UA + RTP receiver ]  ← DOES NOT EXIST YET
                                              │ G.711 µ-law 8 kHz, 20 ms packets
                                              ▼
                                      decode → mono → resample 16 kHz
                                              ▼
                                   phone-adapter.pumpPhoneCall
                                              ▼
                              BotPipeline.feedAudio(0, roomLabel, pcm, epochMs)
                                              ▼
                        @vexa/gmeet-pipeline → stt.v1 → transcript.v1
```

Everything from `decode` down is built and proven offline. Everything above the dashed line is
absent. The adapter's decoder already handles what a trunk actually delivers — **G.711 µ-law at
8 kHz** — as well as 16-bit PCM and 32-bit float, and refuses any other format by name rather than
decoding it as noise. The 8→16 kHz step is linear interpolation, which is adequate for band-limited
speech in a spike; the real receiver should use an anti-aliased polyphase resampler.

`tsMs` is **epoch ms**, the same clock domain the browser bridge stamps. A pump on a relative clock
would date the transcript to 1970 — pinned by a test.

### 5. Trunk / DID provider — a comparison, not a choice

Nothing here is bought, signed up for, or credentialed. Published pay-as-you-go rates, read
2026-09-17:

| Option | Inbound | Number | Shape | Cost to us |
|---|---|---|---|---|
| **Twilio Elastic SIP Trunking** | $0.0040/min (SIP interface) | $1.15/mo local | Media Streams can hand us PCM over a WebSocket — **no SIP stack on our side** | lowest engineering, highest lock-in |
| **Twilio PSTN (no SIP)** | $0.0085/min | $1.15/mo local | same | — |
| **Telnyx Elastic SIP Trunking** | $0.0032/min inbound local; $0.015/min inbound toll-free; or $12/mo per inbound channel | from ~$1/mo | standard SIP; also offers a media-streaming path | cheapest minutes |
| **Plain SIP registrar** (self-hosted Asterisk/FreeSWITCH/Kamailio, or the customer's own PBX) | carrier's rate | carrier's | we terminate SIP ourselves | most engineering, **the only option a self-hoster can actually run** |

The strategic note matters more than the cents: Vexa ships as open source and self-hosts. A bridge
that only works against one vendor's proprietary media-stream API is not a Vexa feature, it is a
Twilio feature we resell. **The recommendation is therefore a provider-agnostic RTP receiver with a
vendor media-stream adapter as a fast path**, not the other way round.

**Cost per minute is dominated by transcription, not telephony.** At $0.0032–0.0085/min the trunk
is rounding error against STT; a dial-in minute costs us essentially what a meeting minute costs us
today, plus a DID per room per month.

### 6. Feature flag

`VEXA_PHONE_PLATFORM=1`, declared in meeting-api's `config.v1.json` with no deploy-surface targets.
Unset — which is every deployment — `tel:`/`sip:` parse to nothing, `resolvePlatform` throws exactly
as before, the adapter refuses to run, and the four web platforms are byte-for-byte unchanged. The
flag is the entire blast radius, and tests assert that on both sides of the language boundary.

## Out of scope, named so nobody assumes otherwise

- **No SIP stack, no trunk, no provider account, no credentials.** Nothing in this repo dials, is
  dialled, or authenticates to a carrier.
- **No video.** A dial-in call carries audio.
- **No multi-party PSTN mixing.** One call, one leg, one room. Conferencing several dial-in legs
  into one meeting is a bridge feature, and a different decision.
- **No per-person attribution in the room** — see §3. Not deferred: ruled out by physics.
- **No inbound dispatch.** Nothing yet answers a call and creates the meeting row.
- **`find_meeting_link` still scans `http(s)` only.** A calendar invite carries a phone number in
  every signature block and in the dial-in fallback of every *web* meeting — a free-text `tel:`
  scan would import footers as rooms and Zoom invites as phone calls. A dial-in address enters
  through a deliberately pasted link, never inference.

## Known rough edges this spike leaves behind

1. **`transcript.v1` has no way to say "this speaker is a room".** `Source` enumerates
   `glow-bound · provisional-cluster-id · caption · merged · chat`, so a dial-in segment currently
   reports `glow-bound` — literally "named at capture", which is true, but it tells a consumer
   nothing about *what kind* of speaker it is. The fix is additive: a `room` value on `Source` (or a
   `speaker_kind`), re-sealed with `pnpm seal:contracts` in a `lane:contract` PR. Deliberately not
   taken inside a spike — widening a sealed contract for unproven work is how contracts rot.
2. **The MCP link parser (`core/meetings/services/mcp/src/vexa_mcp/link_parser.py`) does not know
   `tel:`/`sip:`.** It is a separate implementation from the collector's; only the collector's was
   taught the scheme. Either both learn it, or the duplication is collapsed first.
3. **The terminal's `meetingId.ts` does not know the scheme either** — a dial-in address pasted into
   the dashboard's join form is rejected client-side.
4. **Linear resampling.** Fine for a spike, wrong for production audio quality.

## What a real call still needs

| Step | What it is | Estimate |
|---|---|---|
| RTP receiver | SIP UA registers/accepts, negotiates G.711, receives RTP, jitter buffer, hands PCM to the adapter | 3–4 d |
| Inbound dispatch | a call arrives → resolve the DID to a tenant + room → create the meeting row → start a pipeline → lifecycle.v1 events | 2–3 d |
| DID ↔ room registration | who owns this number, what is this room called, which tenant is billed | 1–2 d |
| Contract + surface work | the `room` speaker kind, the MCP parser, the terminal's join form, docs | 1–2 d |
| Live witness on real hardware | a Poly actually dials the number and a transcript appears | 1 d |

**≈ 8–12 engineer-days to a "a Poly dials a number and the room is transcribed" demo**, from where
this spike leaves the tree — assuming a trunk exists to point it at.

## What the founder must create (nobody else can)

This is the one step an agent must not take. To put a real call behind the adapter:

1. **Open an account at one provider** — Telnyx (cheapest inbound, standard SIP) or Twilio (Media
   Streams removes the SIP stack for the first demo).
2. **Buy one DID** in the demo's country (~$1–1.15/mo) and point it at a SIP URI or a media-stream
   webhook we will supply.
3. **Put the credentials in `vexa-secrets`, never in this repo** — a new
   `vexa-secrets/no-prod/<provider>-dialin.enc.env` holding the API key, the SIP username/password
   and the DID. The repo's `.gitignore` rejects `*.env`; the trunk password is a toll-fraud key, and
   a leaked one is billed by the minute, so it goes in the SOPS store like every other credential.

Nothing in the spike reads those values, and nothing should until the RTP receiver exists.

## Consequences

- The pipeline is confirmed source-agnostic, which is worth more than the dial-in feature itself: a
  desktop recorder, a hardware line-in, or an uploaded file are now all the same shape of work.
- One more platform string exists in the capture vocabulary and must be handled by anything that
  switches on platform — the refusals in `joinMeeting` and `parseInvocation` are what keep that from
  leaking silently.
- The dispatch contract is unchanged, so this spike can be abandoned entirely by deleting two files
  and one flag.
