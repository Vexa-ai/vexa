# ADR-0039 — Operator-supplied native meeting backend

Status: **Accepted** · 2026-10-09 · founder decision: the SDK is not a dependency but an option; an
operator who needs it downloads it. Amends P17 in `docs/docs/governance/architecture.mdx` and records a
Category B licence decision (Qt5Core) under [ADR-0004](0004-open-source-dependency-and-license-policy.md).

Issue: [Retrieve named transcripts from native meetings — integrate the operator-supplied SDK backend](https://github.com/Vexa-ai/vexa/issues/1772).

## Context

The native evaluation can join, capture participant audio and produce named
transcripts. It is not the production bot dispatch path and does not yet persist
transcripts. The web-only `join/src/zoom` backend must remain distinct. SDK is a
backend technology, not a service or a module spanning unrelated concerns.

[Authorized-join policy issue #1289](https://github.com/Vexa-ai/vexa/issues/1289)
proposes refusing the proprietary SDK under P17. Its earlier proposed ADR was
closed unmerged. This ADR does not treat that proposal as accepted, or operator
installation as an automatic exemption from P17.

## Boundary

- `@vexa/join/node` owns native admission, waiting, cancellation and departure.
  Its backend is `join/src/zoom-sdk`, beside web `zoom`. It receives an injected
  native port and has no process-disposal or capture responsibilities.
- `@vexa/zoom-sdk-capture` owns PCM conversion, timing and participant channels
  behind an injected capture port and the shared capture sink contract.
- The bot owns the private subprocess and composition root. The process has no
  network address of its own and is not a standalone SDK service.
- Join and capture use separately sealed `sdk-join.v1` and `sdk-capture.v1` IPC
  contracts. The runtime's validators are those schemas, compiled at load
  (`native-meeting/protocol.cjs`), so they refuse exactly what the schemas refuse;
  `native-meeting/test/protocol.test.mjs` holds them to the goldens.
- Per-participant frames reuse the channel pipeline and injected STT. Native
  identity appears in `speaker_key`; the optional browser glow source is omitted.
- Product integration must reuse the bot lifecycle/transcript sinks and existing
  collector/API. A second database or alternate persistence path is not proposed.

## Decision

The native SDK is an **optional operator-supplied runtime**. P17 now names that
category: it is not a dependency, and it is allowed only if Vexa never commits,
vendors, downloads or bakes it into any artifact; it is off by default and absent
from every default entrypoint and from compose, Helm, Lite and stock dispatch; it
is reached only from a disposable subprocess behind the sealed `sdk-join.v1` and
`sdk-capture.v1` contracts; and it is logged in a manifest row
(`license-exceptions.json`, `operatorSupplied`). `gate:vendor-payload`
(`scripts/check-vendor-payload.mjs`) proves the first, second and fourth in CI, and
of the third that only the declared subprocess loads the addon and that both
contracts are sealed. That the subprocess speaks exactly those contracts, and is
disposed of, is held by the runtime's own tests (`native-meeting/test/`).

Only Vexa-owned wrapper source is tracked. The operator downloads the SDK from
Zoom under Zoom's own terms, which restrict bot and notetaker use, and is
responsible for that licence. The wrapper links Qt5Core (LGPL-3.0), logged as a
Category B exception and never shipped by Vexa. Missing external files fail
explicitly. Enabling stock dispatch, or claiming support, needs its own decision.

Operator app credentials are backend secrets; users authorize separately via
OAuth. Token issuance/refresh/storage and external-meeting attribution belong in
their owning identity/configuration seams before self-service onboarding is
claimed. OAuth, meeting admission and recording permission are separate grants.
Vendor eligibility is separate from technical authentication.

## Consequences and validation

Existing web behavior stays the default. The native path is optional and experimental.
The graph records its modules/contracts. Contract conformance, subprocess lifecycle
failures and browser regression checks accompany the change; vendor-payload absence
is `gate:vendor-payload`.

The live single-speaker probe proves capture-to-transcription only. Persistent API
readback, OAuth, live overlap, sustained capture, native queue bounds and permission
revocation remain open. Disposition: they gate any support claim and any stock
dispatch, not the presence of the optional path. The architecture decision is this
ADR's acceptance and the P17 amendment.
