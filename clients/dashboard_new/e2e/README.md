# dashboard_new e2e — the L4 bulletproof render harness

_dashboard_new/ · gate · a REAL chromium proves the dash-ws brick delivers frames a human's browser renders._

This is the **L4 gate**. The lower gates prove the brick's logic in isolation: `@vexa/dash-ws`'s
own `tsx` test drives the real client over a fake transport and checks the callbacks fire (L2). But a
green node test can still be a **false-green for a human** — node-ws/curl/jsdom can "parse" a frame the
real browser then fails to paint (wrong DOM API, an exception in the render path, a module that won't
load over the real origin). This harness closes that gap:

> **green-in-Playwright ⇒ green-for-the-human's-browser.**

A real chromium loads a static fixture page that runs the **REAL** `@vexa/dash-ws` brick — bundled
straight from its source by esbuild, not re-implemented — over the brick's own `FakeWsTransport`. The
page wires the client's callbacks to the DOM exactly as a dashboard view would, then injects golden
`ws.v1` frames. The spec then asserts the **real DOM** shows them. If chromium painted the status and
the transcript lines, the brick's output is renderable by a person.

## What runs

```
fixtures/ws-entry.ts    re-exports createWsClient + createFakeWsTransport from the dash-ws SOURCE
build-bundle.mjs        esbuild bundles ws-entry.ts → fixtures/ws-bundle.js (a browser ESM module).
                        Runs as Playwright globalSetup, so the page always loads the CURRENT brick.
fixtures/golden.js      the single source of truth for the injected frames + the expected DOM text.
                        Imported by BOTH the page (to emit) and the spec (to assert) so they can't drift.
fixtures/ws-render.html the fixture page: loads the bundle, creates the client over FakeWsTransport,
                        wires onStatus → #status and onTranscript → #transcript .line, fires open
                        (→ subscribe + ping), then emits the golden meeting.status + transcript bundle.
static-server.mjs       a tiny stdlib-only static server for fixtures/ (Playwright webServer).
ws-render.spec.ts       loads the page in chromium, waits for #harness-ready, then asserts #status ==
                        "active" and #transcript holds the golden lines (and no in-page error).
```

### Why http, not `file://`

The fixture loads the bundle + goldens as real ESM modules. Chromium **blocks `import` from
`file://`** (origin `null` → CORS), so a tiny stdlib static server (`static-server.mjs`) serves the
page over `http://127.0.0.1:4317`. No app backend is involved — it only serves the `fixtures/` dir.
Serving over http also mirrors how the page is served on the real stack, so the harness graduates with
no change to the page's module loading.

## The DOM contract

The page exposes exactly what the spec reads:

| selector         | meaning                                                              |
| ---------------- | ------------------------------------------------------------------- |
| `#status`        | text content = the normalized meeting status the brick delivered    |
| `#transcript`    | one `div.line` per confirmed segment, text `Speaker: text`          |
| `#harness-ready` | appended once the goldens have been emitted (deterministic wait)    |
| `#harness-error` | appended with the error text if the in-page wiring threw            |

## Run

```bash
cd clients/dashboard_new/e2e
npm install                 # @playwright/test + esbuild
npx playwright install chromium   # once, if the browser isn't cached
npx playwright test         # → 1 passed
```

`npm run bundle` rebuilds `fixtures/ws-bundle.js` by hand (it's also rebuilt automatically by
globalSetup on every `playwright test`). The bundle is generated, so it's git-ignored.

## This is a real signal, not a tautology

Verified by negative test: break the page's renderer (don't paint `#status`) and the spec **fails** at
the status assertion; restore it and it **passes**. The gate checks the actual rendered DOM, so a
regression in the brick's render path can't sneak through green.

## Graduating to the real stack

Today the page drives frames through the brick's `FakeWsTransport` so the gate is hermetic and fast.
To run this same gate against the **real stack**, swap the fixture's `FakeWsTransport` for a real
`WebSocket`-backed `WsTransport` pointed at a running gateway, and **inject the goldens via redis**
(publish the golden `meeting.status` + `transcript` to the meeting's redis keys the gateway forwards).
Everything above the transport — the page wiring and **every DOM assertion in `ws-render.spec.ts`** —
is unchanged. Green there ⇒ the human's browser renders what the live backend pushes.
