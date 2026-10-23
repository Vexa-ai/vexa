# terminal/src/app

Next.js composition root — boots the DI container and renders the workbench; holds the server-side API proxy routes (the api key stays server-side).

`canvasOrigin.mjs` — the Meeting Canvas origin (`TERMINAL_CANVAS_ORIGIN`, `TERMINAL_CANVAS_PORT`): read by
`server.mjs` (start checks, the canvas listener), the middleware's policies, the root layout (the
`vexa-canvas-origin` meta the page mounts the frame from) and `/canvas-frame` (the origins it answers to).
