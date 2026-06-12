# meet-join — the first brick

Joins a meeting as a participant and reports honest admission state. Platforms: Google Meet (msteams/zoom arrive by extraction).
Contract: `src/_host.ts` — the only surface; the host (vexa-bot) supplies the browser context.
Watch: the hot debug container (Xvfb + humanized X11 + noVNC at `localhost:6080/vnc.html`) — container-only by design: the harness environment is reproducible or it is not evidence. Two network positions, same image:
- `make debug URL=<meet-url>` — local egress (residential)
- `make debug-cloud URL=<meet-url>` — production network position (cloud egress over SSH tunnel); necessary for #444 block-path reproduction: Google keys on IP reputation
Gates: `node scripts/check-isolation.js` + standalone `npm run build`.
Status: promoted in MVP1 (#443); fixtures and oracle formalize with capture.v1 (MVP2).
