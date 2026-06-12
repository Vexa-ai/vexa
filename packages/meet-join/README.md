# meet-join — the first brick

Joins a meeting as a participant and reports honest admission state. Platforms: Google Meet (msteams/zoom arrive by extraction).
Contract: `src/_host.ts` — the only surface; the host (vexa-bot) supplies the browser context.
Watch: `make debug-join URL=<meet-url>` (VNC lens, Mode A) · `make debug-join-docker` (Mode B, full container).
Gates: `node scripts/check-isolation.js` + standalone `npm run build`.
Status: promoted in MVP1 (#443); fixtures and oracle formalize with capture.v1 (MVP2).
