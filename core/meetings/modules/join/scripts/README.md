# join/scripts — gate + debug tooling

- `check-isolation.js` — gate:isolation (P2): walks `src/` and fails if any import escapes the package (not a Node builtin, declared dep, or intra-package path).
- `debug-join.ts` — the live join harness: launches a stealth browser (`playwright-extra`) and runs `joinMeeting` against a real `MEETING_URL`. Run via `tsx` (not compiled / not a published `bin`).
- `debug-rate.sh` — throttle / failure-mode driver (many joins, dialed gap) for admission testing.
- `docker-entrypoint.sh` — entrypoint for the `Dockerfile.debug` hot-reload + noVNC container.
