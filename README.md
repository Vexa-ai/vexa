<div align="center">

<img src="assets/logodark.svg" alt="Vexa logo" width="72"/>

# Vexa

**Open-source, self-hosted meeting bot & transcription API.**

A bot joins your Google Meet, Microsoft Teams, and Zoom calls and streams speaker-attributed
transcripts in real time through an API *you* host — then feeds sandboxed agents that build a
Markdown knowledge base your team owns. Self-hosted, Apache-2.0, air-gap-ready.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.12-informational.svg)](#-status--roadmap)
[![Self-hosted](https://img.shields.io/badge/deploy-self--hosted-success.svg)](#-quickstart)
[![Discord](https://img.shields.io/badge/chat-Discord-5865F2.svg)](https://discord.gg/Ga9duGkVz9)

**[vexa.ai](https://vexa.ai)** still runs the 0.10.6.13 line — it will host **0.12**.

</div>

---

## Why Vexa

Every meeting-AI tool you can buy sends your conversations to *their* cloud and rents you
access back. Vexa inverts that: run the stack yourself, point it at your own models, own what
your meetings become.

No one else has all three:

1. **Vexa is *in* the meeting.** A real bot joins Meet, Teams, and Zoom and streams
   speaker-attributed transcripts live. That bot fleet is the genuinely hard part — every
   "chat with your docs" tool starts *after* a transcript exists. Vexa produces it.

2. **Your knowledge is files you own.** Meetings compile into Markdown in a git repo —
   portable, diffable, greppable. Knowledge as code.

3. **Agents work it, safely.** Sandboxed coding agents read and write that repo like
   developers — isolated ephemeral containers, no egress, thousands in parallel, on Docker
   or your Kubernetes.

> **Only here for the transcription API?** It's a complete standalone product — send a bot,
> read the stream, ignore the agent lane entirely.

---

## Table of contents

- [Quickstart](#-quickstart)
- [How it works](#-how-it-works)
- [The agentic runtime](#-the-agentic-runtime)
- [Agents & your workspace](#-agents--your-workspace)
- [The Terminal: AI-augmented meetings](#️-the-terminal-ai-augmented-meetings)
- [How-to recipes](#-how-to-recipes)
- [Deployment options](#-deployment-options)
- [Deploy & configure](#-deploy--configure)
- [How Vexa is different](#-how-vexa-is-different)
- [For regulated enterprises](#-for-regulated-enterprises)
- [API reference](#-api-reference)
- [Status & roadmap](#-status--roadmap)
- [Community & contributing](#-community--contributing)
- [License](#-license)

---

## ⚡ Quickstart

Self-host the whole stack on one host, then explore it in the Terminal or drive it over the API.
Linux (Ubuntu 24.04) is the production target; a Mac with Docker Desktop works fine for a local
evaluation — everything runs in containers either way.

**Prerequisites** — Docker engine ≥ v26 (`make all` checks), and transcription: a free token at
[vexa.ai/account](https://vexa.ai/account), or self-host the (GPU) transcription unit for a fully
air-gapped setup. Without transcription, bots still join and record — they just produce no text
(`make all` warns when the credentials block in `.env` is empty).

```bash
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
make all      # full Docker Compose stack — seeds .env, builds, prints your API key + URLs
make bot      # build the meeting bot from source (required before a bot can join)
```

When `make all` finishes it prints your key and URLs:

```text
  Terminal UI : http://localhost:13000     # the web workbench
  API gateway : http://localhost:18056     # the API
  API key     : vxa_…
```

### Explore in the Terminal (the fast path)

**The Terminal is the way to see what Vexa can do.** Open **`http://localhost:13000`** — you're
already signed in to a self-host account. From the
workbench you can, with no curl:

- **Send a bot** — paste a Meet / Zoom / Teams / Jitsi URL; a bot joins as a participant.
- **Watch the transcript** stream in live, speaker-attributed, draft-then-confirmed.
- **Chat with your workspace** — ask an agent that has every captured meeting as context, and watch it
  commit what you decide.

### Or drive it over the API

```bash
export API_KEY=vxa_...
export API_BASE=http://localhost:18056

# WIN 1 — send a bot into a live call, then read the transcript as it streams
curl -X POST "$API_BASE/bots" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"platform":"google_meet","native_meeting_id":"abc-defg-hij","bot_name":"Vexa"}'

curl -H "X-API-Key: $API_KEY" "$API_BASE/transcripts/google_meet/abc-defg-hij"

# WIN 2 — ask an agent that has your whole workspace as context (answer streams back as SSE)
curl -N -X POST "$API_BASE/agent/chat" \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"What did we decide in my last meeting?"}'
```

`platform` is `google_meet` · `teams` · `zoom` · `jitsi`; `native_meeting_id` is the code from the join URL. The
agent reply streams as Server-Sent Events — `message-delta` frames carry the text, `commit` frames mark
anything it recorded into your workspace.

---

## 🧩 How it works

One gateway, two domains — **Meetings** (capture) and **Agents** (work the knowledge) — both running on
the same **runtime**: the engine that spawns every bot and every agent in its own sandboxed container.

<div align="center">
  <img src="assets/architecture.svg" width="840"
       alt="One API gateway routes to two domains — Meetings and Agents — both running on one runtime that spawns each bot and agent in its own sandboxed container on Docker, Kubernetes, or Process.">
</div>

A bot and an agent are the **same `runtime.v1` workload** — isolated, ephemeral, reaped on idle — so the
machinery already proven by thousands of meeting bots is exactly what runs your agents. Every arrow stays
inside your network.

---

## ⚙️ The agentic runtime

A CLI coding agent is just a process on Linux. The **runtime** makes that a multi-tenant,
sandboxed execution layer safe to point at real business data — the same engine that already
spawns Vexa's meeting bots in production.

- **Isolated.** Every dispatch gets its own container: no egress except brokered tools, and
  only its granted workspaces exist in its filesystem — enforced by the substrate, not by the
  agent. Agents never run in the control plane.
- **Ephemeral.** A container lives while it works and is reaped on idle; continuity is a
  session file in the workspace. Sub-second starts, thousands in parallel.
- **Orchestration-agnostic.** One `runtime.v1` lifecycle, pluggable substrate — the same
  dispatch runs identically across:

| Backend (`RUNTIME_BACKEND`) | A workload is… | State |
|---|---|---|
| **`docker`** (default) | its own container via the Docker socket — brought up with `make all` | ✅ Shipped (open core) |
| **`process`** | a child process, no Docker socket required | ✅ Available |
| **`k8s`** | a bare **Pod** (`kubectl run --restart=Never`), scheduled across a cluster | ✅ Lifecycle + per-mount workspace isolation; Helm chart in `deploy/helm` |

Same control plane, same worker — only how the container is created changes. One laptop to a
Kubernetes/OpenShift cluster, inside your walls.

---

## 🧠 Agents & your workspace

Capture is the front door; **agents** make the knowledge compound. Every meeting compiles into
your **workspace** — a git repo of Markdown (an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf)
`kg/` bundle) that agents (Claude Code, Codex, …) read and write like developers work a codebase.

> This is [Andrej Karpathy's **LLM Wiki**](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
> pattern, run as a team service. The idea: don't RAG over raw documents — where the model
> rediscovers everything from scratch on every question — have agents **compile** sources into
> structured, interlinked markdown entity pages (people, companies, projects, decisions) so
> knowledge **compounds**. Vexa builds that wiki for you from the richest source there is: your
> meetings. Each call is ingested into entity pages; agents keep them current between calls; every
> answer starts from what your team already knows — on your own servers.

Agents work *any* workspace; a meeting is just one trigger of four — **chat**, **schedule**
(cron), **event** (e.g. incoming email), **finished meeting**. Meetings themselves are scheduled
work: connect your calendar (ICS) and planned meetings appear with attendees — bots
**auto-join**, agents prepare before the call and process after it.

- **Multiplayer.** Team-shared, attributed workspaces — not one person's private notes.
- **Automated.** The bot captures the call; the transcript compiles itself in.
- **Safe by design.** Agents are untrusted and enforce nothing themselves. You, in chat, write
  directly (git is the undo); untrusted input — an email, a web page — runs **propose-only**:
  the agent suggests, a human approves, trusted code applies. Irreversible effects are always gated.

> **Status (honest):** capture, transcription, and speaker attribution are **production**; the
> agent dispatch core is **built and proven live** end-to-end. What's still landing is tracked in
> [Status](#-status--roadmap).

---

## 🖥️ The Terminal: AI-augmented meetings

0.12 ships a **new Terminal UI** built to put the backend's scale — thousands of bots and
agents — to work on your actual week. It opens on your meetings: coming up, live now, to review.

- **An agent in your meeting, with your knowledge.** Open a live call: the transcript streams
  speaker-attributed, and the agent has the live conversation *and* your workspace in context.
  Ask mid-call "what did we promise them last time?" — or research a person, company, or
  contract the moment it comes up, grounded in your wiki.

- **Knowledge built on meetings — and between them.** Every planned meeting gets an agent that
  **prepares the brief before** (who's coming, history, open threads — it interviews *you* for
  what it can't know) and **processes the transcript after**. Arrive prepared, leave with the
  wiki updated.

- **Sharing.** Invite colleagues into a workspace — same wiki, attributed. Share a meeting with
  its attendees — they get the **real-time feed**, not a recording link after the fact.

- **Collaborative, AI-augmented meetings.** Prep a shared workspace together; during the call,
  humans edit the brief while agents stream the transcript in and work the knowledge — one room,
  human and AI participants on the same files.

---

## 📖 How-to recipes

Each is a complete path to one outcome over the [Agent API](#-api-reference). Full guides at
[docs.vexa.ai](https://docs.vexa.ai).

**💬 Chat with your workspace** — ask an agent that has every meeting, email, and note as context; trusted
chat can also record a decision (a git commit).

```bash
curl -N -X POST "$API_BASE/agent/chat" -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"prompt":"Brief me on the Acme account: every meeting, the open decisions, and the next step."}'
```

**🌅 Brief me every morning** — an unattended agent on a cron schedule that commits to your workspace.

```bash
curl -X POST "$API_BASE/agent/routines" -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"Morning brief","cron":"0 8 * * 1-5",
       "prompt":"Brief me from overnight activity — new meetings, decisions, follow-ups due. Write brief/today.md.",
       "run_now":true}'
```

**📝 Report after every meeting** — dispatch a one-shot agent when a call ends (or a routine that sweeps
recent meetings).

```bash
curl -X POST "$API_BASE/agent/invocations" -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"runner":"claude-code","workspaces":[{"id":"u_jane","mode":"rw"}],"trigger":"scheduled",
       "start":{"entrypoint":{"inline":"Write a report for the meeting that just ended: summary, decisions, action items with owners."}}}'
```

**📧 Triage incoming email (safely)** — an event-triggered agent that gets the mailbox **read-only** and can
only *propose* actions as cards; a human approves before anything is written or sent.

```bash
curl -X POST "$API_BASE/agent/events" -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"email.received","source":{"uri":"mailbox://u_jane/INBOX/AB12CD"},
       "plan":{"prompt":"Triage this email into tasks; propose a record for each action item and a draft reply."}}'
```

> **Live-meeting copilot** — cards for people, decisions, and action items *during* the call
> (`POST /agent/meeting/start` → stream `GET /agent/meeting/stream`) — is on the roadmap; see
> [Status](#-status--roadmap).

---

## 🚀 Deployment options

Two ways to run Vexa, one codebase:

**1. Personal / dev — Docker on your Mac, Linux, or Windows machine.**
Single container (`make lite` — the all-in-one Vexa Lite image) or the full Compose stack
(`make all`). **Reuse your Claude subscription**: workers run the official `claude` CLI against
your own Pro/Max credential, which is a covered, credit-metered use under Anthropic's terms for a
personal deployment — your subscription, your turns, your machine. See
[Model credentials & licensing](https://docs.vexa.ai/model-credentials-licensing) for the exact
terms mapping ([Anthropic's Agent SDK plan-usage article](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
is the primary source). You get the full service — bots, transcripts, agents, Terminal — on the
subscription you already pay for.

**2. Cloud — Helm on Kubernetes / OpenShift, scalable to thousands of users.**
The chart in [`deploy/helm`](https://github.com/Vexa-ai/vexa/tree/main/deploy/helm) deploys the same control plane with
`RUNTIME_BACKEND=k8s`: **every bot and every agent is its own Kubernetes workload** (a bare Pod
per dispatch), so capacity is your cluster's scheduler, not a bigger box — built multi-tenant and
multiuser from the start. One compliance rule when you go multiuser: other users' turns must run
on an **API key** (Commercial Terms), never a personal subscription credential — the
[licensing page](https://docs.vexa.ai/model-credentials-licensing) spells out the boundary, and
Settings → Models enforces per-user/global credential resolution. K8s backend status is tracked
honestly in [Status](#-status--roadmap).

---

## 🏠 Deploy & configure

`make all` brings up the full stack via Docker Compose on one Linux host — each service in its own
container, bound to loopback:

| Service | Role |
|---|---|
| **gateway** `:18056` | the one front door — auth, scopes, routing |
| **terminal** `:13000` | the web workbench (proxies `/ws` → gateway) |
| **meeting-api** | bots, transcripts, recordings |
| **agent-api** | the agent control plane — dispatch, chat, routines, events |
| **runtime** | spawns bot + agent containers on demand |
| **admin-api** · redis · postgres · **minio** | keys · bus + scheduler · metadata · object storage (recordings + workspaces) |

- **Runtime backend** — `RUNTIME_BACKEND=docker` (default) or `k8s` (a Pod per dispatch).
- **Transcription is a separate GPU unit** — `make all` runs **GPU-free**; stand up the STT service
  (faster-whisper, OpenAI-compatible) from `deploy/transcription` on any GPU box and point `.env` at it.
  Or use a free hosted token at [vexa.ai/account](https://vexa.ai/account) while testing.
- **Bring your own inference** — point the agent at your own LLM endpoint; no inference leaves the network.
- **Air-gapped** — everything in-VPC, **zero egress** — the posture the regulated verticals require.
- **Targets** — `make all` · `make bot` (build the bot image from source — required, not pulled) ·
  `make lite` · `make up` / `make down` · `make help`. Expose the Terminal via a TLS reverse proxy for
  production; full guide in the [docs](https://docs.vexa.ai).

---

## 🆚 How Vexa is different

The crowded "AI second brain / self-hosted knowledge base" space is full of excellent tools for
reasoning over documents you *already have*. None of them join a live meeting — they consume
transcripts other tools produced. That's the whole point: **capture is the moat, and it sits
upstream of where a document-RAG tool's architecture even starts.**

Against the tools developers actually weigh for meeting capture:

| Capability | **Vexa** | Hosted APIs (e.g. Recall.ai) | DIY (Whisper + your own bot) |
|---|:---:|:---:|:---:|
| Self-hosted / own your data | ✅ | ❌ their cloud | ✅ |
| Real-time transcript API | ✅ | ✅ | 🟡 build it |
| Joins **Meet + Teams + Zoom** | ✅ | 🟡 varies | ❌ enormous effort |
| Speaker attribution | ✅ | ✅ | 🟡 build it |
| Knowledge as files you own | ✅ | ❌ | 🟡 build it |
| Agents over your workspace | ✅ | ❌ | ❌ |
| Open source | ✅ Apache-2.0 | ❌ | ✅ |

Vexa is the one combination the others don't offer: a **permissively-licensed (Apache-2.0)
meeting-bot-API server** that is **self-hosted × real-time × multi-platform × knowledge-you-own.**
And it's *complementary* to the document-RAG and "second brain" tools — feed them Vexa's clean,
attributed transcripts and let them do what they're good at.

The full field — including [Attendee](https://github.com/attendee-labs/attendee) (the other
open-source meeting-bot API) and the local-notetaker tools — is mapped honestly, trade-offs and
all, in [How Vexa compares](https://docs.vexa.ai/comparison).

---

## 🏦 For regulated enterprises

For banks, healthcare, government, and anyone in a regulated industry, the meeting-AI question
isn't "which cloud" — it's "how do we get this **without** a cloud." Vexa is **air-gapped meeting
intelligence** — the sovereign alternative to Microsoft Copilot — built for exactly that buyer.

You don't compete with a notes app here — you replace **Microsoft 365 Copilot** and **Zoom AI
Companion** on the axes they structurally can't move:

| | **Microsoft 365 Copilot / Zoom AI Companion** | **Vexa** |
|---|---|---|
| Deployment | Vendor cloud only | Your cloud, your VPC, or **fully air-gapped** |
| Models | Vendor-hosted, fixed | **Bring your own** — local or hosted LLMs |
| Commercial model | Rented, per-seat subscription | **Owned** — Apache-2.0, no per-seat tax |
| Adaptable | Generic; no custom vocabulary; vendor roadmap queue | **Your engineers extend it directly** — domain vocabulary, underserved languages, custom workflows |
| Meeting platforms | Teams-only / Zoom-only | **Meet + Teams + Zoom** |
| Data control | Transits the vendor's cloud | **Never leaves your perimeter** |
| Extensibility | Closed black box | Open source, API-first |

What that means in practice:

- **Air-gapped** — fully offline, your infrastructure, your models. Nothing phones home.
- **Adaptive** — your engineers implement requirements directly: domain vocabulary, underserved
  languages, custom workflows. No vendor feature queue.
- **Owned, not rented** — deploy once, extend without asking permission. No per-seat tax.
- **Scales inside your walls** — thousands of isolated agents in parallel on Docker or your
  Kubernetes/OpenShift cluster.

**Evaluate it for your org** — the artifacts a security review asks for, in this repo:

| Artifact | What it answers |
|---|---|
| [`architecture.calm.json`](architecture.calm.json) | machine-readable architecture (FINOS **CALM**) — every service and data flow, drift-gated in CI |
| [`SECURITY.md`](SECURITY.md) | how to report a vulnerability |
| [`security-insights.yml`](security-insights.yml) | OpenSSF Security Insights manifest |
| [`license-exceptions.json`](license-exceptions.json) | license gating: Category-A permissive deps, exceptions explicit |
| [`LICENSE`](LICENSE) | Apache-2.0 |

Full review page: [Security & compliance](https://docs.vexa.ai/security-compliance) in the docs.

> Regulated banks and Fortune-500s run Vexa fully air-gapped on their own OpenShift and local LLMs today.

---

## 📡 API reference

Two APIs behind the gateway, authenticated with `X-API-Key`. Base URL: `http://localhost:18056`
(self-host) or `https://api.cloud.vexa.ai` (hosted).

**Meetings API** — capture; usable standalone:

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/bots` | Send a bot into a meeting (`platform`, `native_meeting_id`, `bot_name`, `language`, `task`) |
| `GET` | `/transcripts/{platform}/{native_meeting_id}` | Fetch the real-time transcript (poll while live) |
| `GET` | `/bots/status` | List running bots |
| `DELETE` | `/bots/{platform}/{native_meeting_id}` | Stop / remove the bot |
| `GET` | `/meetings` · `/recordings` | List meetings; list recordings (audio in your own storage) |

**Agent API** — the control plane, under the `/agent/*` prefix (identity is derived from your key, server-side):

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agent/chat` | Chat over your workspace — streams SSE (`message-delta`, `tool-call`, `commit`, `done`, `error`) |
| `POST` | `/agent/invocations` | Dispatch a one-shot agent (e.g. a post-meeting report) |
| `POST` | `/agent/routines` | Create a scheduled (cron) agent routine |
| `POST` | `/agent/events` | Fire an integration event that dispatches an agent (e.g. email triage) |
| `GET` | `/agent/workspace/tree` · `/agent/workspace/file` | Browse and read your Markdown workspace |

`platform` ∈ `google_meet` · `teams` · `zoom` · `jitsi`. Full reference: **[docs.vexa.ai](https://docs.vexa.ai)**.

> **v0.12 note:** live bot-control — `PUT /bots/{…}/config` (change language/task mid-call) and
> `POST /bots/{…}/speak` (TTS into the call) — plus the live-meeting copilot (`/agent/meeting/*`) and
> WebSocket streaming are not yet wired in the open-core stack and return `404` today. Send-a-bot, stop,
> status, transcripts, recordings, agent chat, routines, and events are live.

---

## 🗺️ Status & roadmap

Honest state of the **0.12** line (mirrors the [status page](https://docs.vexa.ai) — never aspirational):

| Capability | State |
|---|---|
| Bot joins **Meet / Teams / Zoom** | ✅ Production |
| Bot joins **Jitsi Meet** (meet.jit.si + self-hosted) | 🆕 Built & offline-proven; live validation pending |
| Real-time transcription (Whisper) + speaker attribution | ✅ Production |
| Redis transcript streaming | ✅ Production |
| Recordings to your own object storage (MinIO) | ✅ Available |
| **Runtime — Docker backend** (container per workload) | ✅ Production |
| **Agent chat / routines / events over your workspace** | ✅ Built & proven live |
| Workspace — git Markdown / OKF `kg/` bundle | 🟡 core proven; bucket-backed store landing |
| **Runtime — Kubernetes backend** (Pod per dispatch) | ✅ Lifecycle + per-mount isolation; Helm in `deploy/helm` |
| Live-meeting copilot (cards as the call runs) | 🔵 Next |
| Calendar sync (ICS) · planned meetings · scheduled auto-join | ✅ Production |
| Shared workspaces & shared meetings (invites, real-time feed) | ✅ Built & proven live |
| Agent chat during a live meeting (live transcript + workspace in context) | ✅ Built & proven live |
| WebSocket transcript multiplex | 🔵 Planned (poll today) |
| At-rest encryption (workspace · transcript · tokens) | 🔵 Planned |
| Mid-call bot config / speak | 🔵 Returns 404 in open-core |

✅ Production · 🟡 In progress · 🔵 Planned

---

## 🤝 Community & contributing

- **Docs** — [docs.vexa.ai](https://docs.vexa.ai)
- **Discord** — [discord.gg/Ga9duGkVz9](https://discord.gg/Ga9duGkVz9)
- **Issues & PRs** — welcome. See [`SECURITY.md`](SECURITY.md) to report vulnerabilities.

Vexa is built in the open. If you self-host it, extend it, or run it air-gapped somewhere interesting,
we'd love to hear about it.

---

## 📄 License

[Apache-2.0](LICENSE). Own it, run it, fork it, ship it. It's an investment, not a rental.
