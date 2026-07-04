# Vexum

**Compound your organization's intelligence — turn meetings into knowledge your agents act on, in real time, on infrastructure you control.**

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-docs.core.vexa.ai-blue)](https://docs.core.vexa.ai)
[![Live demo](https://img.shields.io/badge/demo-core.vexa.ai-brightgreen)](https://core.vexa.ai)
[![FINOS](https://img.shields.io/badge/FINOS-contribution%20proposed-orange)](https://github.com/finos/community/issues/420)

> **▶ Try the live demo: [core.vexa.ai](https://core.vexa.ai)** — the Vexum Terminal workbench, running this codebase.
> **📖 Full documentation: [docs.core.vexa.ai](https://docs.core.vexa.ai)**

---

The decisions that move money and risk are made in **meetings** — live, spoken, gone when the call ends.
Firms can't point AI at that data to fix it: too sensitive to leave their boundary, too risky to let an
agent act unsupervised. So the **data → signal → action** loop stays slow and lossy.

**Vexum closes that loop in real time, inside your boundary.** A bot joins Google Meet / Zoom / Teams
natively and captures real-time, speaker-attributed transcripts (Whisper included). **Sandboxed CLI
coding agents** — the proven coding-agent loop (Claude Code, Codex, …) pointed at knowledge instead of
code — work the live transcript into your own workspace: **knowledge as code**, versioned files agents
read, write, and commit like a codebase, live during the call and after. Every external or irreversible
action is **propose → approve → apply** — the human stays the gate.

- **Safe & scalable** — each agent runs in an isolated, ephemeral container (no egress except brokered tools; thousands in parallel).
- **Sovereign by default** — self-hosted, air-gappable, identity-scoped, with a [FINOS CALM](https://github.com/finos/architecture-as-code) model in-repo.
- **Two composable domains** — **Meetings** (real-time capture + transcript API) and **Agents** (sandboxed agents over a Markdown workspace). Run either alone or together.

## Quickstart

```bash
git clone https://github.com/Vexa-ai/vexa-core.git && cd vexa-core
make all      # full stack via Docker Compose — seeds .env, brings everything up, prints your API key + URLs
make bot      # build the meeting bot from source — needed before a bot can join a meeting
```

The gateway API comes up at `http://localhost:18056` and the terminal web workbench at
`http://localhost:13000`. Air-gapped + bring-your-own-inference options are in
[Deployment](https://docs.core.vexa.ai/deployment).

**Prefer to look before you build? → [core.vexa.ai](https://core.vexa.ai)** runs the same stack, hosted.

## How it works

A unit of work — a **dispatch** — is one agent, in one container, over a workspace, authorized by a
signed identity token, fired by the scheduler. Five primitives compose everything:
**workspace · agent · container · identity · scheduler** ([Concepts](https://docs.core.vexa.ai/concepts)).

- **Knowledge as code** — meetings and docs become a versioned Markdown [workspace](https://docs.core.vexa.ai/concepts#workspace); git is the durable state and the undo. The structure is yours to design, like a codebase.
- **Identity as a chain of custody** — every dispatch carries a short-lived signed token (*subject · launcher · scope*); the runtime attests the workload (SPIFFE/SPIRE); every boundary verifies the token, never the agent; tool calls exchange it for an audience-scoped credential (Keycloak / RFC 8693). The audit log resolves every effect to *(subject · launcher · scope)*. See [Identity & trust](https://docs.core.vexa.ai/architecture/identity-and-trust).
- **Propose-only governance** — untrusted-input (email/web) or irreversible (external send) actions are gated; compromise an agent via prompt injection and it still can't exceed the token's scope. See [Governance](https://docs.core.vexa.ai/architecture/governance).

## Repository layout

| Dir | Role |
|---|---|
| `core/runtime/` | kernel — spawn/execute workloads + mount the workspace |
| `core/meetings/` | capture — join → capture → transcript |
| `core/agent/` | execution — transcript → governed action |
| `core/identity/` | access · accounts · tokens · audit |
| `core/gateway/` | the edge — auth · routing · WS fan-out |
| `clients/` | terminal workbench · SDKs · extensions |
| `calm/` | FINOS CALM model — `architecture.calm.json` + controls + patterns |
| `deploy/` · `docs/` | deployment topologies · documentation + ADRs |

Architecture deep-dive: [docs.core.vexa.ai/architecture](https://docs.core.vexa.ai/architecture/execution) — modules, dispatch, execution, streaming, governance, identity/trust.

## Status

Current state ([full tracker](https://docs.core.vexa.ai/roadmap/status)):

- **Proven in production (pre-existing Vexum):** the runtime (meeting bots as browser workloads), transcription → `transcript.v1`, redis streaming.
- **Built & proven live — the dispatch core:** a `unit.v1` dispatch runs in a runtime-spawned isolated container over a bind-mounted workspace, carrying a per-dispatch signed identity token, streaming events to SSE; chat memory is durable. Verified end-to-end on Docker and through the terminal.
- **On the roadmap:** auth-spine + owner-checks, bucket/transcript/token encryption, calendar + email integrations. `agent · identity · gateway` are hardening; contract-conformance gates + golden fixtures ship in-repo.

## FINOS contribution

Vexum is proposed to the [Fintech Open Source Foundation](https://www.finos.org/) — see
[finos/community#420](https://github.com/finos/community/issues/420). The repository is Apache-2.0 and
ships the FINOS governance set: [LICENSE](LICENSE) · [NOTICE](NOTICE) · [MAINTAINERS](MAINTAINERS.md) ·
[CODE_OF_CONDUCT](CODE_OF_CONDUCT.md) · [CONTRIBUTING](CONTRIBUTING.md) · [SECURITY](SECURITY.md).

## License

[Apache-2.0](LICENSE). Transcription uses faster-whisper / CTranslate2 (MIT); model weights are
downloaded at runtime, not redistributed. No copyleft in the contributed tree.
