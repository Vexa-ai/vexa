# modules/ — the bricks (contributor guide)

Each brick is one concern behind a contract, and the contract is the **only**
coupling between bricks. So fixing a bug is *routing*:

> **Find the brick that owns your symptom → reproduce it with no live meeting →
> fix it there.** This page is that router + runbook.

Architecture & rationale live in [`../MANIFEST.md`](../MANIFEST.md); what's
tested *right now* lives in [`../PIPELINE-RELEASE.md`](../PIPELINE-RELEASE.md).
This file is for **a contributor fixing a thing**.

---

## 1. The map

```mermaid
flowchart LR
    URL([meeting URL]):::ext --> J[join]
    J -- page --> C[capture]
    C -- capture.v1 --> P["pipeline<br>(mixed ‖ multistream)"]
    P -- separated-transcript.v1 --> A["speaker-attribution<br>(cluster-binder ‖ caption-mapper)"]
    A -- transcript.v1 --> D[delivery]:::soon
    D --> CL([client]):::ext

    C -. tap .-> R[recording]
    R -- recording.v1 --> M([media file]):::ext

    C -. capture.v1 .-> REC[recorder]
    REC -. records .-> FX[("fixture<br>replays everything downstream")]:::ext

    class J,C,P,A,R,REC brick
    classDef brick fill:#dbeafe,stroke:#2563eb,color:#1e3a8a;
    classDef ext fill:#f3f4f6,stroke:#9ca3af,color:#374151;
    classDef soon fill:#fef3c7,stroke:#d97706,color:#92400e,stroke-dasharray:5 3;
```

<sub>Solid boxes = bricks · arrows carry the **contract** named on them · dotted = tap/record · amber dashed `delivery` = still in the bot, not yet a module.</sub>

| Brick | Does | Contract in → out |
|---|---|---|
| [join](join/) | gets the bot into the meeting | URL → admitted page |
| [capture](capture/) | audio + signals out of the page (browser-only) | page → `capture.v1` |
| [pipeline](pipeline/) | audio → text, split by speaker (no names) | `capture.v1` → `separated-transcript.v1` |
| [speaker-attribution](speaker-attribution/) | opaque ids → real names | `separated-transcript.v1` + hints → `transcript.v1` |
| [recording](recording/) | the meeting media file | tap → `recording.v1` |
| [recorder](recorder/) | records the `capture.v1` fixture (head of the chain) | `capture.v1` → fixture |

> `delivery` (ship `transcript.v1` to clients) is still in the bot, not yet a module.

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
| **speaker identification degrades with 5+ participants** | **pipeline** (diarizer) | #317 |
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

Three surfaces, mapped to the chain: **the extension** (live, page→downstream),
**fixtures** (offline, capture→downstream), **join** (the page-entry, alone).

### A · Live & interactive — the product extension *(start here)*

One terminal runs the whole backend **hot**; the real product extension drives
it. Covers **capture → pipeline → attribution → delivery, online.**

*One-time setup:*
```bash
cd modules/pipeline && cp .env.example .env     # then put TRANSCRIPTION_SERVICE_TOKEN in .env
cd ../../services/vexa-extension && npm run build  # then chrome://extensions → Load unpacked → dist/
```

*Each session — the hot loop:*
```bash
cd modules/pipeline       && npm run dev    # hot backend: pipeline+attribution+gateway, reloads on any brick edit
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
cd modules/pipeline
npm run replay:mixed -- <fixture-dir>       # mixed (zoom/teams) → separated-transcript.v1
npm run replay       -- <fixture-dir>       # multistream (gmeet)
cd ../speaker-attribution && npx tsx scripts/attribute-fixture.ts <fixture-dir>   # naming → transcript.v1
```

**Judging mixed-pipeline quality (zoom/teams)** — benchmark against Deepgram and
*look with your own eyes*: `npm run bench:mixed` (full Deepgram → 2-min window of
interest → faithful real-time playback) then `npm run bench:view`
(http://localhost:8077 — Deepgram vs Vexa side-by-side, colour-per-speaker, synced
audio playback). Full guide: [modules/pipeline/README.md](pipeline/README.md#debugging-the-mixed-pipeline-zoom--teams).

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

---

## 4. Where to help (coverage = the to-do map)

✅ proven · 🟡 partial · ⚪ untested · ❌ broken/missing · — N/A

| | Google Meet | Zoom | MS Teams |
|---|---|---|---|
| capture | 🟡 per-participant live | ✅ | 🟡 needs toolbar gesture |
| chat | ⚪ no reader | ✅ | ❌ no reader |
| pipeline | (multistream 🟡) | ✅ live + replay | 🟡 quality issues |
| names | 🟡 via `speaker-joined` | ✅ | ❌ active-speaker selectors stale |
| fixture collected | 🟡 old | ✅ | ❌ none |

**Open work, by priority:**
1. **Teams names** — `msteams-speakers.ts` active-speaker selectors stale (`hints=0`).
2. **Teams chat reader** — only `zoom-chat.ts` exists.
3. **gmeet multistream** end-to-end confirm; collect **gmeet + teams fixtures**.
4. **`cluster-name-binder` dedup** (lives in `pipeline` + `speaker-attribution`).
5. **`separated-transcript.v1`**: thread `words[]`; meeting-relative timestamps.

---

**The one rule:** a brick imports contracts, other bricks' **published packages**
(`@vexa/*`, never their `src/`), and third-party deps — never `services/`, never
another brick's internals. Services import bricks. Full spec: [MANIFEST §3b](../MANIFEST.md).
