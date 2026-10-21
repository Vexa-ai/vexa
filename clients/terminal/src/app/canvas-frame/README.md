# app/canvas-frame

`/canvas-frame` — the document a Meeting Canvas view runs in (`canvas/frame/CanvasFrame.tsx`).
`middleware.ts` serves it with `canvasFrameContentSecurityPolicy`: `sandbox allow-scripts` (an
opaque origin), `connect-src 'none'`, `frame-ancestors 'self'`. The terminal page frames it from
`canvas/runtime.tsx` and talks to it by `postMessage` only (`canvas/frame/protocol.ts`).
