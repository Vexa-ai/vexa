# @vexa/join — get the bot into the meeting (the first brick)

Joins a meeting as a participant and reports **honest admission state** — the one
job at the head of the chain. Platforms: Google Meet, MS Teams, Zoom (web client;
the native-SDK path stays in `vexa-bot`).

**Why it's its own brick:** join doesn't fail on *data flow*, it fails on **IP
reputation, geo, rate-limits, and bot-detection** — a completely different
debugging axis from the rest of the pipeline. Isolating it lets you reproduce a
join failure by *moving the egress IP*, not by touching transcription.

- **Contract** — `src/_host.ts` is the only surface; the host (`vexa-bot`)
  supplies the browser context. The brick never imports the bot.
- **The oracle is the host, not the brick.** `admitted=true` on a DOM selector is
  a *claim*; the meeting's own People panel ("waiting" vs "in the meeting" + the
  count) is the truth. Cross-check them — that's how the admission false-positive
  was caught.

## Hot debug container
Runs the brick from source in a reproducible env (Xvfb + humanized X11 + noVNC at
`localhost:6080/vnc.html`). The image bakes only the *environment*; `src/` is
mounted live and run via tsx, so a host edit + re-run is instant — no rebuild.
Container-only by design: the harness environment is reproducible or it is not
evidence. Same image, two network positions:

- `make debug URL=<meet-url>` — local (residential) egress.
- `make debug-cloud URL=<meet-url> CLOUD_HOST=<host>` — the same image from another
  egress IP (source rsync'd live). `bbb` = a second household vantage; a throwaway
  datacenter VM = the production position (prod bots speak from datacenter ranges)
  — needed to reproduce the datacenter-only block (#444).

Full agent brief — the loop, the admission oracle, what's proven vs open — is in
[CLAUDE.md](CLAUDE.md).

## Gates
`node scripts/check-isolation.js` (no import escapes the package) · `npm run build`
(standalone, tsc clean).
