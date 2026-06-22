# @vexa/dash-vnc-view — the per-bot noVNC viewer

_dashboard_new/ · module · VIEW · the #5 per-bot VNC view._

A single **presentational** React component, `<VncView>`, that embeds the per-bot noVNC session in an
`<iframe>`. The bot container runs noVNC; the gateway will route `/b/{id}/vnc/*` to it. This brick is
**props in, DOM out** — NO store, NO fetch, NO ws. The caller composes the full per-bot URL and injects
it as `vncUrl`; an empty `vncUrl` renders the loading placeholder. Behavior is carved clean from the
vendored dashboard's `browser-session-view.tsx` / `meetings/[id]/page.tsx` iframe (same `<iframe src>` +
`allow="clipboard-read; clipboard-write"`, same loading fallback), without the page's coupling.

## Props contract

```ts
interface VncViewProps {
  /** The fully-composed per-bot noVNC URL (gateway `/b/{id}/vnc/...`). Empty string while the bot /
   *  session token is still resolving — that drives the placeholder. */
  vncUrl: string;
  /** Optional accessible title for the embedded viewer iframe. Default "Bot VNC viewer". */
  title?: string;
  /** Optional placeholder text shown while `vncUrl` is empty. Default "Connecting to bot…". */
  placeholderText?: string;
}
```

- `vncUrl` **non-empty** → renders `<iframe data-testid="vnc-iframe" src={vncUrl}
  allow="clipboard-read; clipboard-write" />` filling its container (`width/height: 100%`, `border: 0`).
- `vncUrl` **empty** → renders the placeholder (`[data-testid="vnc-placeholder"]`, `role="status"`)
  with `placeholderText`. No iframe is mounted.

The caller composes `vncUrl` exactly as the gateway will route it, e.g.
`/b/{token}/vnc/vnc.html?autoconnect=true&resize=scale&reconnect=true&path=b/{token}/vnc/websockify`.

## Surface

`VncView` (default + named) · type `VncViewProps`. Front door: [`src/index.ts`](src/index.ts).
Pure presentational — identical props give identical DOM. No `@vexa/dash-contracts` runtime import: the
sole input is a `string` URL the caller composes; the brick stays a leaf VIEW.

## Verify

`pnpm --filter @vexa/dash-vnc-view run build` — `tsc` clean (the tsconfig adds the `DOM` lib + `react-jsx`).

The **L4 bulletproof gate** is a real chromium (Playwright) over golden props:
`pnpm --filter @vexa/dash-vnc-view test` (= `playwright test --config e2e/playwright.config.ts`).
`globalSetup` esbuilds [`e2e/vnc-entry.tsx`](e2e/vnc-entry.tsx) (the REAL `<VncView>` from source + react)
into `e2e/vnc-bundle.js`; a tiny stdlib static server serves the two fixtures; the spec
([`e2e/vnc-render.spec.ts`](e2e/vnc-render.spec.ts)) asserts the rendered DOM:

1. golden `vncUrl` ([`vnc-url.html`](e2e/vnc-url.html)) → an `<iframe>` with `src` === the golden per-bot
   noVNC URL renders, and the placeholder does not.
2. empty `vncUrl` ([`vnc-empty.html`](e2e/vnc-empty.html)) → the placeholder renders with its text, and
   no iframe is mounted.

green-in-Playwright ⇒ green-for-the-human's-browser. If chromium can't launch, run
`pnpm --filter @vexa/dash-vnc-view run install:browser` (`playwright install chromium`) first.
