# vnc-view — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The #5 per-bot noVNC viewer VIEW brick: a presentational React component <VncView vncUrl> that embeds the per-bot noVNC session in an iframe (the gateway routes /b/{id}/vnc/* later). Props in, DOM out — no store, no fetch, no ws. Empty vncUrl renders a loading placeholder. L4-proven in a real chromium (Playwright): mount over a golden vncUrl → an <iframe src> renders; mount empty → the placeholder renders.

