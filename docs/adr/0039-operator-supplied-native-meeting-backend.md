# ADR-0039 — Operator-supplied native meeting backend

Status: **Proposed — requires maintainer architecture/licensing review**

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

## Proposed boundary

- `@vexa/join/node` owns native admission, waiting, cancellation and departure.
  Its backend is `join/src/zoom-sdk`, beside web `zoom`. It receives an injected
  native port and has no process-disposal or capture responsibilities.
- `@vexa/zoom-sdk-capture` owns PCM conversion, timing and participant channels
  behind an injected capture port and the shared capture sink contract.
- The bot owns the private subprocess and composition root. The process has no
  network address of its own and is not a standalone SDK service.
- Join and capture use separately sealed `sdk-join.v1` and `sdk-capture.v1` IPC
  contracts. Runtime validators must reject unknown properties like the schemas.
- Per-participant frames reuse the channel pipeline and injected STT. Native
  identity appears in `speaker_key`; the optional browser glow source is omitted.
- Product integration must reuse the bot lifecycle/transcript sinks and existing
  collector/API. A second database or alternate persistence path is not proposed.

## Dependency and authorization boundary

Only Vexa-owned wrapper source is tracked. SDK archives, headers, libraries and
compiled addons are downloaded/built by the operator outside the source tree.
No dependency manifest, automatic download, default entrypoint or stock image
installs or activates the SDK. Missing external files fail explicitly.

This limits redistribution/default-install exposure. It does not establish that
an optional proprietary runtime is acceptable under P17: that is an explicit
review question. Do not change P17, claim an exception or enable stock dispatch
without a maintainer decision.

Operator app credentials are backend secrets; users authorize separately via
OAuth. Token issuance/refresh/storage and external-meeting attribution belong in
their owning identity/configuration seams before self-service onboarding is
claimed. OAuth, meeting admission and recording permission are separate grants.
Vendor eligibility is separate from technical authentication.

## Consequences and validation

Existing web behavior stays the default. The prototype is opt-in and experimental.
The graph records its modules/contracts. Contract conformance, subprocess lifecycle
failures, vendor-payload absence and browser regression checks accompany the PR.

The live single-speaker probe proves capture-to-transcription only. Persistent API
readback, OAuth, live overlap, sustained capture, native queue bounds and permission
revocation remain acceptance rows. The PR stays draft until those rows and this
architecture decision have concrete dispositions.
