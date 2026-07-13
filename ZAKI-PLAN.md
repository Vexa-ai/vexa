# ZAKI Minutes — current delivery plan

## Goal

Ship a self-hosted Minutes spoke that visibly joins a consented meeting, produces a tenant-isolated
transcript, lets the ZAKI agent read it through a bounded read-only contract, and can erase all raw
meeting data without leaving transcript residue in the brain.

Production activation is a separate owner go/no-go. The plan keeps all runtime flags off until the
privacy and live-evidence gates are green.

## Current objective — fork foundation

Expected result: a ProjectNuggets fork exists at a pinned upstream tag, keeps both remotes and full
Apache-2.0 provenance, documents its product boundary and upgrade path, and passes the upstream gate
suite without any ZAKI runtime or contract change.

Definition of done:

- GitHub fork and local checkout exist with `origin` and `upstream` remotes.
- Baseline tag and SHA are recorded.
- The Vexa-versus-alternatives decision is source-cited and captured in an ADR.
- The full upstream gate command is run and its exact result recorded in the coordination handoff.
- No chart, secret, database, object store, feature flag, or cluster resource is added.

## Staged path

### Stage 1 — contract adjudication

Resolve WP-15's four read-contract decisions with the Brain/Contracts steward. Add the accepted
Minutes profile as a sealed, versioned schema with golden vectors and explicit negative controls.

Proof: schema/golden gates pass; wrong token, path/header user mismatch, cross-tenant item, expired
item, unsafe redirect, and over-budget body all fail closed.

### Stage 2 — Minutes-owned read and privacy core

Implement the read profile as an adapter over the Vexa meeting/transcript store. Add ZAKI tenant
mapping, capture-policy evidence, retention scopes, TTL workers, deletion, content-free erasure
receipts, and backup-restoration bounds. Do not add brain access.

Proof: isolated module and service tests cover owner-only reads, expiry, atomic disappearance from
index/search/item, meeting deletion, account erasure, and zero raw transcript in logs or artifacts.

### Stage 3 — lifecycle and webhook adaptation

Add stable ZAKI meeting identity, visible bot naming, hub-driven scoped provisioning, HMAC timestamp
and replay protection for finalization events, idempotency, failure-state mapping, and quota refusal.

Proof: Compose drives duplicate/replayed/out-of-window webhooks, denied joins, early ends, forced bot
termination, retry, and tenant concurrency limits without orphaned workloads.

### Stage 4 — live development gates

Run consented, non-production meetings on Meet first, then Teams and Zoom. Include English, Arabic,
and a long-meeting/chunking case. Capture sanitized fixtures from the live output for replay.

Proof: bot-visible human observation, lifecycle logs, API transcript, storage census, deletion census,
and transcript-quality evaluation corroborate. Unvalidated platforms remain explicitly unclaimed.

### Stage 5 — hub and infrastructure pilot

Only after Stages 1–4: add the hub client/webhook/UI and the zaki-infra chart with immutable images,
dedicated secrets, default-deny policies, observability, capacity limits, and flags off by default.

Proof: gated staging journey from meeting invite through agent answer, new-session recall, draft email,
meeting forget, account erasure, TTL expiry, restore drill, and zero cross-tenant access.

## Critical path

Contract adjudication → privacy/read core → webhook/lifecycle adaptation → live meeting evidence →
hub/chart pilot. UI design and capacity planning may proceed in parallel, but neither can activate the
spoke before retention, erasure, and isolation are proven.

