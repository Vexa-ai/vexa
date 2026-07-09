# ADR 0013 — Report state from evidence, not intent (P21); govern the clients

**Status:** accepted · 2026-06-19 · introduces **P21** · extends P9/P18 to the clients

## Context

The extension shows **"Listening — capturing 0 stream(s)"**: its UI flips to the active/"Listening"
state the moment capture is *requested* (Start clicked, offscreen told to begin), **not** when audio
frames are *observed flowing*. The tab-capture stream id is an ephemeral, gesture-granted resource the
browser owns — it is often never minted (no toolbar click) or **silently invalidated on reload**. So the
displayed state asserts success that reality (zero frames) never gets to contradict.

This is the same shape as two failures already seen this milestone: STT returned "no transcript" without
saying *why* (`402`), and the eval `capture` tool reported "unhealthy" when it wasn't. **The system
asserts a state it has not verified** — and it does so most damagingly in the **client**, which was the
least-governed code in the tree: the extension has **zero tests**, no liveness, no governed state
machine. The principles and gates stopped at the service boundary, exempting exactly the layer where the
user meets failure.

## Decision

Adopt **P21 — Report state from evidence, not intent**, and extend governance to the clients.

- **State is earned by an observed signal.** A success/active status is shown only once the confirming
  signal is observed. Capture is **"Listening" only after the first audio frame is relayed**
  ("first-frame-observed"); before that it is a distinct **"starting / waiting for signal"** state, never
  "working". `started ≠ working`.
- **A no-frames watchdog.** While "active", if no frame is observed for N seconds the state drops to
  **"no-signal"** and is surfaced (panel + `/telemetry`) with the actionable cause ("0 frames — tab
  capture not minted? click the Vexa toolbar icon on the tab; lost on reload"). This is the **P18
  liveness clause applied client-side**: absence of an expected signal is itself a reported state.
- **The clients are in scope (P9).** The extension's capture state machine gets its first **L2 tests**
  (fake chrome + WS), and a `gate:client-liveness` gate. Principles and gates do not stop at the service
  boundary — the client is where the user experiences the system.
- **Adapt the resource's failure mode (P5).** The `chrome.tabCapture` stream id's invalidation-on-reload
  is adapted: the extension detects loss (the watchdog), re-acquires where it can, and reports where it
  cannot — rather than holding a dead stream behind a "Listening" label.

## Consequences

- "Listening over silence" becomes impossible to *represent*: the UI cannot show active without a frame
  having flowed, and a stalled feed flips visibly to "no-signal" with a fix-it hint. The commonest
  capture failure is now self-diagnosing at the point of use.
- The through-line is closed: claims are evidence-backed at every layer — **dev-time (P19)**, **runtime
  server (P18 liveness)**, **runtime client (P21)**.
- Cost: a frame-observed event + a watchdog timer + the extension's first tests. Small, and it retires a
  recurring "the extension is flaky" class of report.
