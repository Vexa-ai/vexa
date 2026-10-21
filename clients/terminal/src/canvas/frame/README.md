# canvas/frame

The isolated document a Meeting Canvas view runs in, and the wire to it.

- `protocol.ts` — the only messages the terminal page and the frame exchange, the frame's path
  (`/canvas-frame`) and its sandbox (`allow-scripts`, never `allow-same-origin`). The page reads a
  frame message only through `readFromFrame` and runs an action only through `runAction`.
- `compile.ts` — turns a view's source into a component (`new Function`). Imported by the frame
  alone; nothing on the terminal page evaluates a view.
- `CanvasFrame.tsx` — the frame's app: takes the source and the meeting from its parent, compiles,
  trial-renders hidden, promotes, and posts its status, height and the actions a person pressed.

The frame document is served under `canvasFrameContentSecurityPolicy` (`app/contentSecurityPolicy.ts`):
an opaque origin, no connections, framed by the terminal's own pages only.
