---
id: test/admit-bot
type: validation
requires: [test/bot-lifecycle]
produces: [BOT_ADMITTED]
validates: [bot-lifecycle]
docs: [features/bot-lifecycle/README.md]
mode: machine
skill: /test-admit-bot
---

# Admit Bot

> Follows [RULES.md](RULES.md). This procedure owns its scripts — fix them when they don't match reality.

Use Playwright on the meeting host's browser session to admit the bot from the waiting room. Platform-specific selectors.

## Inputs

| Name | From | Default | Description |
|------|------|---------|-------------|
| GATEWAY_URL | test/infra-up | — | API gateway URL |
| API_TOKEN | test/api-full | — | API token with bot scope |
| SESSION_TOKEN | test/create-live-meeting | — | Browser session token (from `data.session_token`) |
| MEETING_PLATFORM | test/create-live-meeting | google_meet | Platform |
| NATIVE_MEETING_ID | test/create-live-meeting | — | Meeting ID (e.g. `pmm-rwrr-gud`) |

## Browser access pattern

Connects to the browser via gateway CDP proxy — **from the host, not docker exec**:

```javascript
const browser = await chromium.connectOverCDP('$GATEWAY_URL/b/$SESSION_TOKEN/cdp');
const page = browser.contexts()[0].pages().find(p => p.url().includes('meet.google.com'));
```

## Script

```bash
eval $(./testing/admit-bot.sh GATEWAY_URL API_TOKEN SESSION_TOKEN MEETING_PLATFORM NATIVE_MEETING_ID)
```

See [admit-bot.sh](admit-bot.sh) for implementation.

## Steps

1. Connect to browser via `$GATEWAY_URL/b/$SESSION_TOKEN/cdp`, find meeting tab
2. Find and click Admit button (Google Meet selectors)
3. Poll bot status via API — must transition to `active` within 30s

> assert: admit button clicked, bot becomes active
> on-fail: Google Meet UI may have changed selectors. Screenshot saved on failure.

## Outputs

| Name | Description |
|------|-------------|
| BOT_ADMITTED | `true` if bot successfully admitted and active |

## Failure modes

| Symptom | Cause | Fix | Learned |
|---------|-------|-----|---------|
| No admit button visible | Bot hasn't reached waiting room yet | Wait longer, or re-check bot-lifecycle status | |
| Admit button exists but click doesn't work | Google Meet anti-automation | Try different selector strategy, add delays | |
| Bot admitted but stays in joining | Bot failed to detect admission (DOM observer missed it) | Check bot's meeting join detection code | |
| Screenshot shows "You can't join this meeting" | Meeting host left or meeting ended | Create a fresh meeting | |
| Clicked "Admit all" but bot still waiting | Google Meet shows confirmation dialog "Admit all?" | Must click "Admit all" twice — once in panel, once in dialog | Two-step admit: panel → confirmation dialog |
| `text=/Admit/i` opens people panel instead of admitting | Top-right "Admit 1 guest" opens panel, not admits directly | Use people panel's "Admit all" button, then confirm dialog | The top-right button is a panel toggle, not an admit action |
| Browser session dies during test | Container idle timeout or resource limits | Keep browser session alive; create session close to when needed | Don't create browser session too early — containers have timeouts |

## Docs ownership

After this test runs, verify and update:

- **features/bot-lifecycle/README.md**
  - DoD table: update Status, Evidence, Last checked for item #2 (bot reaches active state in live meeting) — this test is the one that triggers the `awaiting_admission -> active` transition by clicking Admit
  - States table: verify the `awaiting_admission -> active` transition is sourced from `bot_callback` (not `user` or `system`) as documented — check the bot's `data.status_transition` after admission
  - Note in the DoD: "07 owns `requested -> joining -> awaiting_admission -> active`" should reference that 08-admit-bot performs the actual admission click that completes this chain
