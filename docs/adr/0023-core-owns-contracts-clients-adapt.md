# ADR 0023 — The core owns its contracts; clients adapt (dependencies point inward)

**Status:** proposed · 2026-06-22 · rides `lane:contract` · composes ADR-0007 (vendored dashboard debt)

## Context

The 0.12 carve defines clean public contracts (`api.v1`, `ws.v1`, …). The dashboard is **vendored from
main** (ADR-0007) and still speaks main's older protocol — e.g. for live status it consumes
`{type:"meeting.status", payload:{status}}` with the spelling `needs_human_help`, while `ws.v1` defines
`{type:"bot_status", status}` and the backend's `BotStatus` enum value is `needs_help`. When wiring the
live WS-status path, the impedance was first absorbed **in the core**: the meeting-api publisher emitted
the dashboard's frame shape and remapped its own enum value to the dashboard's spelling. That pushes a
single client's legacy vocabulary into the shared contract — every other client, and the contract itself,
would then have to follow the dashboard's quirk.

## Decision

- **The core owns its contracts and emits the clean canonical shape.** Each domain publishes/serves its
  contract verbatim — meeting-api emits `ws.v1` `BotStatus` (`{type:"bot_status", status, meeting_id}`)
  with the `BotStatus` enum value, never a client's renaming.
- **Clients adapt; dependencies point inward.** Adapters and legacy / vendored clients absorb every
  impedance mismatch **on their own side** — the dashboard consumes `bot_status` and maps
  `needs_help → needs_human_help` in `clients/dashboard/src/lib/ws-status.ts`. A consumer's legacy name,
  field shape, or quirk is **never** pushed upstream into the core.
- **Translate at the client boundary, never bend the core to a client.** The gateway forwards core
  payloads verbatim — it is not a translation layer. When a client speaks an older protocol, the
  anti-corruption translation lives in that client.
- **Smell that flags a violation:** a core publisher emitting a frame/field named for, or mapped to, a
  specific client's vocabulary. The fix moves the translation out to the client.

## Consequences

- meeting-api publishes clean `ws.v1` `bot_status`; the vendored dashboard's `ws-status.ts` adapts it. The
  contract stays client-agnostic, so a new SDK/client consumes the canonical shape with no core change.
- Composes ADR-0007: the vendored dashboard's debt is paid **on the dashboard side**, not by corrupting the
  core — and is retired when the dashboard is refactored, with no core churn.
- `AGENTS.md` hard rules carry this inline ("The core owns its contracts; clients adapt").
- **Known reconciliation:** `ws.v1`'s documented transcript frame (`transcription_segment`) is out of sync
  with the shape both the publisher and the dashboard actually use (`{type:"transcript", confirmed, pending}`);
  the contract should be reconciled to the shipped reality on a future `lane:contract` change.
