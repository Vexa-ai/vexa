# modules — the dashboard bricks

Every concern of dashboard_new lives here as a self-contained **brick**: one public front door
(`src/index.ts`), a `README.md`, and a test whose exit code is the signal (logic bricks → `tsx`;
view bricks → a Playwright `e2e/` spec). Bricks depend only on each other's front doors, never internals.
The consumed truth is `core/gateway/contracts/` (ws.v1 + api.v1). See each brick's README for its concern.

