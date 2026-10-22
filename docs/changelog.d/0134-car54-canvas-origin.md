- **Meeting Canvas views can run on an origin of their own.** Set `TERMINAL_CANVAS_ORIGIN` (Compose,
  Lite) or `terminal.canvasHost` (Helm) and the canvas frame is served from that host or port alone:
  a view that navigates its own frame reaches only the canvas host, which serves the frame and the
  build's files and refuses everything else. Unset, the frame stays on the terminal's origin and the
  terminal warns at start. See [Meeting Canvas isolation](/meeting-canvas-isolation).
- **A canvas view's scripts load from its own origin only.** The frame's policy no longer carries
  `'strict-dynamic'`, under which a script element a view created loaded from any host.
- **The terminal refuses to be framed by another origin** and serves no script, stylesheet or font
  to one but its build files (read from the browser's `Sec-Fetch-*` headers, sent over HTTPS and on
  `localhost`).
