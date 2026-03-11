# Vexa Bot Experiment Agent

You are inside the vexa-bot Docker container with Playwright, Chromium, and the full bot source code. Debug and improve the bot by controlling the browser directly.

## Environment

- **Bot source**: `/app/vexa-bot/core/src/` (TypeScript, baked into experiment image)
- **Built output**: `/app/vexa-bot/core/dist/`
- **Display**: `DISPLAY=:99` (Xvfb running)
- **Workspace**: `/workspace/` (persists on host)
- **Node modules**: `/app/vexa-bot/core/node_modules/`

## Bot Logs (Meeting+Agent Mode)

When running alongside a live meeting bot, the bot's real-time logs are streamed to `/tmp/bot.log`. **Always check this first** when asked about the meeting or bot status:

```bash
# See recent bot activity
tail -100 /tmp/bot.log

# Follow bot logs in real-time (use timeout to avoid blocking)
timeout 10 tail -f /tmp/bot.log

# Search for specific events
grep -i "error\|warn\|joined\|admitted\|recording\|caption" /tmp/bot.log | tail -30
```

Key bot log patterns:
- `[MeetingFlow]` — join/leave lifecycle, admission, recording state
- `[Captions]` — transcript capture activity
- `[Chat]` — in-meeting chat messages
- `[AudioCapture]` — recording pipeline status
- `ERROR` / `WARN` — problems to investigate

When the user asks you to follow the bot or check what's happening, read the bot logs. You can also connect to the bot's browser via CDP to inspect the page directly.

## Browser Control

Launch a Chromium browser:
```bash
node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({
    headless: false,
    args: ['--no-sandbox', '--disable-blink-features=AutomationControlled', '--remote-debugging-port=9222']
  });
  const page = await browser.newPage();
  await page.goto('https://meet.google.com');
  console.log('Browser ready. CDP at http://localhost:9222');
  // Keep alive
  await new Promise(() => {});
})();
"
```

Connect to a running browser (e.g. after bot script started one):
```bash
node -e "
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.connectOverCDP('http://localhost:9222');
  const pages = browser.contexts()[0].pages();
  console.log('Connected. Pages:', pages.map(p => p.url()));
})();
"
```

## Key Files

**Selectors** (the fragile stuff — start here):
- `/app/vexa-bot/core/src/platforms/googlemeet/selectors.ts`
- `/app/vexa-bot/core/src/platforms/msteams/selectors.ts`

**Flows**:
- `/app/vexa-bot/core/src/platforms/shared/meetingFlow.ts`
- `/app/vexa-bot/core/src/platforms/googlemeet/{join,admission,recording,leave,removal}.ts`
- `/app/vexa-bot/core/src/platforms/msteams/{join,admission,recording,leave,removal}.ts`

**Chat monitoring**: `/app/vexa-bot/core/src/services/chat.ts`

## Workflow

1. Check bot logs: `tail -100 /tmp/bot.log` (if meeting+agent mode)
2. Connect to running browser: `chromium.connectOverCDP('http://localhost:9222')`
3. Inspect the page, test selectors
4. Edit source in `/app/vexa-bot/core/src/`, rebuild: `npm run build`
5. Re-test

To run the full bot manually:
```bash
cd /app/vexa-bot/core
BOT_CONFIG='{"platform":"google_meet","meetingUrl":"https://meet.google.com/xxx","botName":"TestBot","connectionId":"test","nativeMeetingId":"xxx","meeting_id":1,"token":"test","redisUrl":"redis://redis:6379/0","container_name":"test","automaticLeave":{"waitingRoomTimeout":300000,"noOneJoinedTimeout":600000,"everyoneLeftTimeout":120000}}' node dist/docker.js
```
