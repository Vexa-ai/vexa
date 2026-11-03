# app/debug/readme

The README lab (0.13.4 S7): the workspace README as two viewers see it side by side (`trees.json`,
produced by the server's own `page_render.render` on synthetic sources: Dmitry the owner, Valeriia a
reader), plus references and paste, the simplified Share, a room's Pinned strip and a message's
long-press sheet. `/api/refs/*`, `/api/sessions*`, `/api/people/mentionable`, `/api/pins`, `/api/bots`
and `/api/desktop/card/read` are answered by an in-browser stub. A production build answers 404
(`gate.ts`). `?theme=light`, `?view=dmitry|valeriia|both`. Calls are kept on `window.__readmeLab.calls`.
