# ADR 0035 — Authorized Zoom join goes through RTMS, not the Meeting SDK (P17)

**Status:** proposed · 2026-08-20 · constrained by **P17** (ADR-0004) · decides
[#1289](https://github.com/Vexa-ai/vexa/issues/1289) · selects
[#706](https://github.com/Vexa-ai/vexa/issues/706)

## Context

Vexa joins Zoom through **Zoom's own first-party web client** and nothing else.
`buildZoomWebClientUrl()` rewrites `zoom.us/j/<id>?pwd=…` to `app.zoom.us/wc/<id>/join`, and
`joinZoomMeeting()` drives that page with Playwright — type a guest name, clear the consent banner,
grant mic, click Join
([`core/meetings/modules/join/src/zoom/join.ts`](../../core/meetings/modules/join/src/zoom/join.ts)).
There is a second identity mode, `BotConfig.authenticated`, but it is not an API credential: it is a
**persistent Chromium profile whose cookies a human placed there by signing in once over VNC**
([`@vexa/remote-browser` `provisionLogin()`](../../core/meetings/modules/remote-browser/src/login.ts)).
One shared Zoom identity, provisioned by hand, reused across bots.

Neither mode is an *authorized* join in Zoom's sense. We hold **no ZAK, no On-Behalf-Of (OBF) token,
no join token, and no OAuth grant from the meeting's account.** The word "authorized" here is Zoom's:
a participant whose entry is backed by a credential the host's account issued.

Zoom is closing the door on the unauthorized path, and our own code already watches it close.
`detectZoomBotBlock()` matches the wall Zoom serves to automated browsers —

> "We detected you may be a bot. Automated bots aren't allowed to join this meeting or webinar and
> must use Zoom RTMS. … Sign in to join"

— and fails fast with the reason `zoom_requires_rtms`
([`zoom/admission.ts`](../../core/meetings/modules/join/src/zoom/admission.ts)). That wall is keyed to
the meeting/account, not to IP reputation: verified identical from a datacenter IP and a residential
IP on the same meeting. **The sanctioned path is named in the error text by Zoom itself.**

Meanwhile [`attendee-labs/attendee`](https://github.com/attendee-labs/attendee), the closest
open-source competitor, ships "ZAK token, OnBehalf token and Join token support" on its public
roadmap. This is the one named platform capability a direct OSS competitor advertises and we do not,
and it lands on `send-a-bot-to-the-meeting` — **558 clean docs reads**, our fourth-most-read job
([`job-matrix.yaml`](https://github.com/DmitriyG228/biz/blob/main/graph/traffic/read-models/job-matrix.yaml)).

So the obvious move is "add ZAK." **The obvious move is not available to us, and the reason is
structural rather than a matter of effort.**

**ZAK and OBF are Meeting SDK credentials.** Zoom's authorization docs define them only against the
Meeting SDK; there is no parameter by which `app.zoom.us/wc/` accepts one
([Meeting SDK authorization](https://developers.zoom.us/docs/meeting-sdk/auth/)). Adopting ZAK is
therefore not a patch to our join path — **it is replacing Zoom's web client with the Zoom Meeting
SDK as our client stack.**

And the Meeting SDK is proprietary. Zoom grants a *"limited, revocable, non-exclusive,
non-transferable, non-assignable, non-sublicensable"* licence, retains all IP, and **prohibits
sublicensing and redistribution**; third-party distribution needs written approval
([Zoom API License and Terms of Use](https://www.zoom.com/en/trust/legal/zoom-api-license-and-tou/)).
**P17** forbids exactly this: *"source-available/proprietary … are forbidden. The platform must drop
into a regulated org with zero licence encumbrance"*
([architecture.mdx](../docs/governance/architecture.mdx), enforced by `gate:licenses` per ADR-0004).
[`zoom/README.md`](../../core/meetings/modules/join/src/zoom/README.md) already records the call in
one line: *"No native Zoom SDK (proprietary, Cat-X under P17 — deliberately not promoted)."*

The cost is not abstract. Vexa is Apache-2.0 and self-hostable, and `self-host-vexa` is our
**most-read job at 972 reads**, the one coordinate where the field survey calls us *"provably ahead —
only Apache-2.0 bot API at scale."* A non-sublicensable SDK cannot ship inside an Apache-2.0
self-hostable image; every self-hoster would have to accept Zoom's SDK terms themselves.
**Matching Attendee on ZAK by vendoring the Meeting SDK would trade our strongest position for our
fourth-strongest.** That is the trade this ADR exists to refuse.

## Decision

**Authorized Zoom access goes through Zoom RTMS (Realtime Media Streams). We do not vendor the Zoom
Meeting SDK, and we do not implement ZAK, OBF, or join tokens.**

Three consequences follow directly.

**1. RTMS is a lane, not a join.** RTMS delivers live audio, video, and transcript data
server-side over a WebSocket, driven by `meeting.rtms_started` / `meeting.rtms_stopped` webhooks. It
is reachable with **native WebSockets** — no `zoom/rtms` C++ wrapper, which would re-import the
Cat-X problem ([RTMS docs](https://developers.zoom.us/docs/rtms/),
[native WebSockets guide](https://developers.zoom.us/blog/realtime-mediastreams-websockets/)). No
browser enters the meeting, so no bot appears in the participant list and the anti-bot wall is not
in the path. The lane's build is [#706](https://github.com/Vexa-ai/vexa/issues/706); this ADR settles
*which* lane, not how it is built.

**2. RTMS is per-tenant, and that is a product fact, not a detail.** RTMS requires a **General App**
in the Zoom Marketplace (Server-to-Server OAuth and Webhook-only apps do not work), installed **on
the account whose meetings are streamed**, with auto-start enabled under *"Auto-start apps that
access shared realtime meeting content"*, holding `meeting:rtms:read` plus per-medium scopes
([getting started](https://developers.zoom.us/docs/rtms/meetings/getting-started/)). So RTMS covers
**a customer's own meetings**, once their admin installs our app. It does **not** cover pasting an
arbitrary external Zoom link. Anyone promising "authorized join for any Zoom URL" on the back of RTMS
is promising something RTMS does not do.

**3. The web client stays, and keeps its two modes.** RTMS does not replace the browser join; it
covers the meetings the browser cannot legitimately enter. Anonymous guest join remains the default
for meetings that permit it. The `authenticated` cookie-profile mode remains for meetings requiring a
signed-in user. Both are licence-clean and neither is deprecated by this decision.

**The honest capability statement**, which supersedes any "we support Zoom" shorthand:

| Path | Credential | Covers | Licence | Ships in self-host |
|---|---|---|---|---|
| Web client, anonymous | none | meetings that admit guests | clean | ✅ today |
| Web client, `authenticated` | human-provisioned cookie profile | meetings requiring a signed-in user | clean | ✅ today |
| **RTMS** | OAuth General App installed on the **customer's** account | that customer's own meetings, incl. bot-blocked ones | clean (native WebSocket) | ✅ planned |
| Meeting SDK + ZAK/OBF | OAuth → ZAK/OBF | external meetings, authorized | **Cat X — refused** | ❌ never |

## Consequences

- **Two live docs pages currently promise the opposite and must be corrected when this ADR is
  accepted.** [`changelog.mdx:80`](../docs/changelog.mdx) says BYO OBF/ZAK plus the native SDK path is
  *"planned back in the 0.12.x Zoom track"*, and
  [`roadmap/status.mdx:118`](../docs/roadmap/status.mdx) carries the same row. Both predate the P17
  reading above. They are rows A2/A3 of [#1289](https://github.com/Vexa-ai/vexa/issues/1289) and are
  deliberately **not** changed by this ADR's own PR — the docs should not assert a decision that is
  still `proposed`.
- **We will not reach ZAK parity with Attendee, by choice, and we say so publicly.** The comparison
  is not "Attendee has a feature we lack" but "Attendee vendors a proprietary SDK and we do not."
  Any `/comparison` or docs claim on this must state the licence reason; silence reads as a gap.
- **`zoom_requires_rtms` currently degrades on the way out.** The join layer knows the exact cause and
  passes it to `callBlockedCallback`, but the `AdmissionError` it throws carries outcome `denial`, so
  it lands in `CompletionReason.AWAITING_ADMISSION_REJECTED`
  ([`lifecycle/machine.py`](../../core/meetings/services/meeting-api/src/meeting_api/lifecycle/machine.py))
  — indistinguishable from a host clicking Deny. An operator cannot tell "this meeting needs RTMS"
  from "the host refused us," which is precisely the signal that would tell a customer to install the
  app. A distinct terminal reason is the smallest useful follow-on.
- **A Zoom Marketplace General App becomes a product surface we own** — listing, review, scopes,
  install flow, per-tenant credential storage. That is onboarding and trust-boundary work
  (ADR-0003), not just an ingest lane.
- **This ADR is revisited only if Zoom relicenses the Meeting SDK permissively, or ships an
  authorized join credential usable without it.** Neither is on their public roadmap today.
- **Not decided here:** the RTMS ingest implementation, its mapping onto the meeting lifecycle, or
  whether the hosted plane offers it before self-host. Those belong to the ingest issue.
