# canvas/frame

The isolated document a Meeting Canvas view runs in, and the wire to it.

- `protocol.ts` — the only messages the terminal page and the frame exchange, the frame's path
  (`/canvas-frame`) and its sandbox (`allow-scripts`, never `allow-same-origin`). The page reads a
  frame message only through `readFromFrame` and runs an action only through `runAction`.
- `compile.ts` — turns a view's source into a component (`new Function`). Imported by the frame
  alone; nothing on the terminal page evaluates a view.
- `lockdown.ts` — what the frame takes away before any view runs: WebRTC, so a view cannot carry the
  data it was given out through a STUN host. It also records why a navigation to this origin cannot
  be cancelled (an opaque-origin document gets no Navigation API events).
- `CanvasFrame.tsx` — the frame's app: takes the source and the meeting from its parent, compiles,
  trial-renders hidden, promotes, and posts its status, height and the actions a person pressed.

The frame document is served under `canvasFrameContentSecurityPolicy` (`app/contentSecurityPolicy.ts`):
an opaque origin, no connections, framed by the terminal's own pages only. The embedding page's
`frame-src 'self'` refuses a navigation of the frame to another host; a navigation to this origin
reaches only this server, without the reader's cookies. The frame response carries
`X-DNS-Prefetch-Control: off`.
