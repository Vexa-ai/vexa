# server

Helpers for `server.mjs`, the terminal's custom Node server, kept as plain `.mjs` so the server imports
them without a build step. `imageOptimizer.mjs` answers `/_next/image` with a 404 before Next sees it
(the optimizer is off and its cache directory is not writable in the runtime image).
