# modules/ — the bricks (contributor guide)

Each brick is one concern behind a contract, and the contract is the **only**
coupling between bricks. So fixing a bug is *routing*:

> **Find the brick that owns your symptom → reproduce it with no live meeting →
> fix it there.** This page is that router + runbook.

This file is for **a contributor fixing a thing** — the brick map + runbook.

> **Two lanes over a shared engine (0.11):** both lanes are now carved.
> `mixed/` (Zoom/Teams, segmentation + hints) → `@vexa/mixed-capture-*` +
> `@vexa/mixed-pipeline`; `gmeet/` (per-channel audio, glow-bound name) →
> `@vexa/gmeet-capture` + `@vexa/gmeet-pipeline`. Both ride the shared engine
> (`@vexa/transcribe-buffer`, `@vexa/transcribe-whisper`, `@vexa/capture-codec`).
> `speaker-attribution`, the diarizer monolith, and `separated-transcript.v1` are
> **dropped** (names are capture-bound for gmeet, hints-only for mixed). The host
> that composes the lanes lives in [`services/vexa-desktop`](../services/vexa-desktop/).

---

## 1. The map

```mermaid
flowchart LR
    URL([meeting URL]):::ext --> J[join]
    J -- page --> C["capture<br>(gmeet ‖ mixed/capture/*)"]
    C -- "gmeet-capture.v1 ‖ mixed-capture.v1" --> P["pipeline<br>(gmeet ‖ mixed-pipeline)"]
    P -- transcript.v1 --> D[delivery]:::soon
    D --> CL([client]):::ext

    C -. tap .-> R[recording]
    R -- recording.v1 --> M([media file]):::ext

    C -. capture .-> REC[recorder]
    REC -. records .-> FX[("fixture<br>replays everything downstream")]:::ext

    class J,C,P,R,REC brick
    classDef brick fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;
    classDef ext fill:#f3f4f6,stroke:#9ca3af,color:#374151;
    classDef soon fill:#fef3c7,stroke:#d97706,color:#92400e,stroke-dasharray:5 3;
```

<sub>Solid boxes = bricks · arrows carry the **contract** named on them · dotted = tap/record · amber dashed `delivery` = still in the bot, not yet a module. Naming is folded into each lane (gmeet = capture-bound glow name; mixed = hints-only namer) — there is no separate attribution brick.</sub>

| Brick | Does | Contract in → out |
|---|---|---|
| [join](join/) | gets the bot into the meeting | URL → admitted page |
| [capture (gmeet)](gmeet/capture/) | Google Meet per-channel audio + glow name | page → `gmeet-capture.v1` |
| [mixed/capture/*](mixed/capture/) | Zoom/Teams mixed audio + active-speaker hints | page → `mixed-capture.v1` |
| [pipeline (gmeet)](gmeet/pipeline/) | channel-routed transcription, name at capture | `gmeet-capture.v1` → `transcript.v1` |
| [mixed/pipeline](mixed/pipeline/) | segmentation cut + hints-namer (no clustering) | `mixed-capture.v1` → `transcript.v1` |
| [shared/*](shared/) | the engine: `capture-codec` · `buffer` (LocalAgreement) · `whisper` (stt.v1) | — |
| [recording](recording/) | the meeting media file | tap → `recording.v1` |
| [recorder](recorder/) | records the capture fixture (head of the chain) | capture → fixture |

> `delivery` (ship `transcript.v1` to clients) is still in the bot, not yet a module.

**Lanes, shared engine & tooling** — each brick's own README has its surface +
files; start at the lane:
- **Lanes:** [`gmeet/`](gmeet/) (per-channel audio, name bound at capture) ·
  [`mixed/`](mixed/) (one mixed stream, hints namer) — the two topologies.
- **Shared engine:** [`shared/`](shared/) — `capture-codec` (wire) · `buffer`
  (LocalAgreement-3 confirm) · `whisper` (stt.v1).
- **Host:** [`services/vexa-desktop`](../services/vexa-desktop/) composes the lanes
  into the all-Node backend (ingest + gateway + recording tee).
- **Eval:** [`eval/`](eval/) drives a **live** meeting full of ground-truthed
  speaker bots to debug the stack hot; [`mixed/eval/`](mixed/eval/) scores the
  mixed lane **offline** against a YouTube/Deepgram fixture.

---

## 2. If… → then debug

Symptom-first triage, drawn from **what people actually report** (issue refs are
real examples — read them for the full repro).

| Reported as… | → then debug | seen in |
|---|---|---|
| `admission_false_positive` — bot exits "admitted" while still in the lobby, or never joins | **join** | #171 #166 #377 #123 |
| "can't join this link" / "fails to join google meet" | **join** | #380 #286 #431 #343 |
| `awaiting_admission_rejected` — can't tell lobby-timeout from host-denial | **join** | #421 |
| page goes black after "Ask to join" → reported as rejected-by-admin | **join** | #432 |
| Zoom/Teams blocks the bot as "automated" before admission | **join** | #345 |
| bot doesn't leave when everyone's gone / leaves when people join | **join** | #190 #189 |
| joins / reaches ACTIVE **but no audio** — `audioTracks=0`, ScriptProcessor stops, zero-segment | **capture** | #115 #204 #409 #237 #251 |
| **0 transcripts on real meetings** — per-speaker scrape hits `<audio>` DOM that's gone | **capture** | #318 |
| whole-meeting **empty / blank transcript** | **capture** *(no audio reaching pipeline)* **or pipeline** *(STT down)* — check whether audio is flowing | #445 |
| **"cannot reach transcription service"** / transcription not working | **pipeline** (STT) | #146 #223 |
| **repetitions / hallucinated phrases** in the transcript | **pipeline** | #104 |
| transcription **stops on long meetings** (>~25 min / >1500 s) | **pipeline** | #232 #157 |
| **speaker identification degrades with 5+ participants** | **mixed/pipeline** (hints-namer) | #317 |
| **recording lost / overwritten / "failed after N mins" / won't store** | **recording** | #404 #412 #224 #268 #282 |

*From dev-debugging this session, not yet filed (same brick — **capture**):* mic
bleed ("everything is You"), `hints=0` → no speaker names, audio mutes when
capture runs (page audio graph touched).

> **Some reports route OUT of the transcript spine** — Teams chat-send that never
> delivers (#133) is *acts*; transcripts hidden by `session_uid` mismatch (#96) is
> *store*; admin-dashboard 401s are *auth*. Those aren't bricks here yet — see the
> issue triage for the full map.

> **Some symptoms don't name a brick on their own — they need a *signal*.**
> *Blank transcript* is the canonical example: `capture` (no audio) vs `pipeline`/STT
> (audio flowing, no text). You tell them apart by **whether audio frames are reaching
> the pipeline** — visible in the `live-stack` heartbeat locally; in production this
> must be **surfaced as health/status** on the API (`GET /transcripts` and the WS return
> `transcribing | degraded | no-audio | stt-down` alongside segments — issue #423).
> That health surfacing is **cross-cutting, not one brick**: every brick emits its own
> status (admitted? audio? stt-reachable?), and the delivery/API layer aggregates and
> exposes it. It's a `health.v1`-style *contract* + a consumer — the observability plane,
> not a module.

---

## 3. How to debug

Surfaces, mapped to the chain: **the extension** (live, page→downstream),
**fixtures** (offline, capture→downstream), **join** (the page-entry, alone), and
the **synthetic eval** (drive a live meeting with ground-truthed speaker bots).

### A · Live & interactive — the product extension *(start here)*

One terminal runs the whole backend **hot**; the real product extension drives
it. Covers **capture → pipeline → delivery, online** (naming is folded into each lane).

*One-time setup:*
```bash
cd services/vexa-desktop  && cp .env.example .env   # then put TRANSCRIPTION_SERVICE_TOKEN in .env
cd ../vexa-extension      && npm run build          # then chrome://extensions → Load unpacked → dist/
```

*Each session — the hot loop:*
```bash
cd services/vexa-desktop  && npm run dev    # hot backend: both lanes + gateway, reloads on any brick edit
cd services/vexa-extension && npm run dev   # (optional, other terminal) rebuilds + auto-reloads the extension on edit
```
In the extension **sidepanel**: `ingestUrl` = `ws://localhost:9099/ingest`,
`gatewayUrl` = `http://localhost:8056`, **API key** = anything (not validated),
**Language** = your meeting's (not `auto`). Join a meeting → **Start** → the
transcript forms live (confirmed + pending + names).

Edit a brick → backend hot-reloads → re-**Start** in the extension to see it.
*Gotchas:* zoom/teams need the **toolbar-icon gesture** to attach remote audio;
use **headphones** so your mic isn't transcribed as the whole room.

### B · Offline & replayable — fixtures (the output of `capture`)

A **`capture.v1` fixture is everything the page produced** — audio + speaker
hints + chat, on the real meeting clock. It is the input to the *entire*
downstream chain, so **one fixture debugs pipeline + attribution + delivery with
no meeting, deterministically.** Because capture emits the same `capture.v1`
however it's assembled (bot in-process · extension WS · production), the recorder
collects it the same way everywhere — *including teeing a production ingest to
pull a real meeting.*

**Three ways to get a fixture** (the input `gate:replay` and the integration tests need):

1. **Collect live, on the fly** — run the recorder and drive the product extension on any meeting:
   ```bash
   cd modules/recorder && npm run capture     # WS recorder on :9099 → writes to the fixture store
   ```
   Set the sidepanel `ingestUrl` = `ws://localhost:9099/ingest`, join → Start → talk → **Stop**. Fixture lands in `$VEXA_FIXTURE_CACHE/capture/v1/<platform>-<id>/` (default `~/.vexa/fixtures`).
2. **A meeting you control** — same flow on your own test meeting (no host needed for the extension).
3. **Dump from a deployed Vexa** — pull a *real* (production) meeting's `capture.v1` to reproduce a prod bug locally. *Requires the deployment to **retain raw `capture.v1` for a rolling window** (e.g. N days) — the recorder tee on the ingest path + a `dump` command.* **Not yet wired** — tracked as the "probe prod for fixtures" item.

Fixtures live in `$VEXA_FIXTURE_CACHE`, **never in the repo**; `FIXTURE_S3=1` pushes
to the shared store. (Real-meeting transcripts are sensitive — keep them private.)

*Replay it downstream* — no meeting:
```bash
cd services/vexa-desktop
npm run replay -- <fixture-dir>             # gmeet (channel-routed) → transcript.v1
```

**Judging mixed-pipeline quality (zoom/teams)** — the agentic eval vehicle in
[`mixed/eval`](mixed/eval/) benchmarks `@vexa/mixed-pipeline` against Deepgram and
renders a side-by-side, timestamp-aligned playback page (audio + segmentation
boundary pointers). Pull a YouTube fixture, run a region, serve the page — full
guide in [mixed/eval/CLAUDE.md](mixed/eval/CLAUDE.md).

### C · `join`, in isolation
`join` is debugged differently — it fails on **IP reputation, geo, and
rate-limits**, not on data flow. Its own hot harness (full brief in
[modules/join/README.md](join/README.md) + `CLAUDE.md`) runs the brick from
source in a reproducible browser; you watch the bot's actual screen at noVNC.

```bash
cd modules/join
make image                                              # once (~2 min): Linux + Xvfb + humanized X11 + noVNC
make debug URL="https://meet.google.com/xxx-xxxx-xxx"   # run from THIS egress; watch http://localhost:6080/vnc.html
grep -E "JOIN-STATE|ADMIT-DUMP|RESULT" /tmp/mj-run.log  # read the verdict
```
**The oracle is the host, not the brick.** `admitted=true` on a DOM selector is a
*claim*; the host's People panel ("waiting" vs "in the meeting" + the count) is
the truth — cross-check them (that's how the admission false-positive was caught).

Three axes, because join usually fails on the **network position**, not the code:
- **isolation** — `make debug URL=…` (above): reproduce a join/admission bug locally.
- **egress / location** — `make debug-cloud URL=… CLOUD_HOST=<host>`: the *same image from a different IP*. `bbb` = a residential vantage; a throwaway datacenter VM = the production position. The egress IP is the only variable — this is how you reproduce a datacenter-only block (#444).
- **rate-limit / throttle** — `make debug-rate URLS=urls.txt COUNT=10 GAP=30`: repeated joins at a controlled cadence from a fixed egress, logging outcomes → find the attempt frequency where the platform starts blocking the IP (run it on `CLOUD_HOST` for a datacenter IP).

**The split:** extension = online, downstream of the *page* · fixtures = offline,
downstream of *capture* (any deployment, incl. prod) · join = the page-entry,
by itself — debugged by **moving the egress IP**, not the code.

### D · Synthetic meeting — speaker bots on demand ([`eval/`](eval/))

To put *controllable, ground-truthed* load on the live stack (surface A),
[`modules/eval`](eval/) sends N speaker bots into a real meeting via the Vexa
service API and has them speak known TTS scripts on a timeline you dial
(#speakers, length, overlap). Because the script text is known, the captured
transcript is **scored automatically** (completeness / leakage / attribution) —
not just eyeballed. Drives the desktop hot rig, or any deployment that can launch
bots; the *same* workflow also runs on a real meeting with real people.

```bash
cd modules/eval        # fill secrets.env first — see eval/CLAUDE.md
./bin/eval.sh launch   # bots join (staggered, IP-safe)
./bin/eval.sh drive    # bots speak the timeline → truth.jsonl
./bin/eval.sh judge    # score the live transcript vs ground truth
```

---

## 4. Where to help (coverage = the to-do map)

✅ proven · 🟡 partial · ⚪ untested · ❌ broken/missing · — N/A

| | Google Meet | Zoom | MS Teams |
|---|---|---|---|
| capture | 🟡 per-participant live | ✅ | 🟡 needs toolbar gesture |
| chat | ⚪ no reader | ✅ | ❌ no reader |
| pipeline | 🟡 channel-routed | ✅ live + replay | 🟡 quality issues |
| names | ✅ glow↔channel (at capture) | ✅ | ❌ active-speaker selectors stale |
| fixture collected | 🟡 old | ✅ | ❌ none |

**Open work, by priority:**
1. **Teams names** — `@vexa/teams-capture` active-speaker selectors stale (`hints=0`).
2. **Teams chat reader** — only `@vexa/zoom-capture` has one.
3. **Bot rewire** — `services/vexa-bot` is the last consumer still on the dropped
   monolith: it imports `@vexa/pipeline` (`SileroVAD`/`VadSpeakerState`, deleted) +
   `@vexa/capture`. Re-point it onto the carved lanes (`@vexa/gmeet-*`,
   `@vexa/mixed-*`, `@vexa/capture-codec`).
4. **Fixtures** — collect **gmeet + teams** `capture.v1` fixtures (zoom done).

---

**The one rule:** a brick imports contracts, other bricks' **published packages**
(`@vexa/*`, never their `src/`), and third-party deps — never `services/`, never
another brick's internals. Services import bricks. Each module's
`npm run check:isolation` enforces it.
