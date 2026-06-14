# meet-join — the first brick

Joins a meeting as a participant and reports honest admission state. Platforms: Google Meet, MS Teams, Zoom (web client — the native-SDK path stays in vexa-bot).
Contract: `src/_host.ts` — the only surface; the host (vexa-bot) supplies the browser context.
Watch: the **hot debug container** — runs the brick from source in a reproducible env (Xvfb + humanized X11 + noVNC at `localhost:6080/vnc.html`). The image bakes only the environment; `src/` is mounted live and run via tsx, so a host edit + re-run is instant — no rebuild. Container-only by design: the harness environment is reproducible or it is not evidence. Two network positions, same image:
- `make debug URL=<meet-url>` — local egress (residential)
- `make debug-cloud URL=<meet-url> CLOUD_HOST=<host>` — another egress IP, source rsync'd live (hot). `bbb` = second household vantage; a throwaway Linode VM = the production position (prod bots speak from datacenter ranges) — necessary for #444 block-path reproduction
Gates: `node scripts/check-isolation.js` + standalone `npm run build`.
Status: promoted in MVP1 (#443); fixtures and oracle formalize with capture.v1 (MVP2).
