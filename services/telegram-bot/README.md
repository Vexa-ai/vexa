# Telegram Bot

## Why

Agents are only useful if you can reach them where you already are. The dashboard requires a browser, and raw API calls require a terminal. Telegram puts your agent in your pocket — message it from your phone to join a meeting, ask about a transcript, or trigger automation. It's also the entry point for the scheduler: when a scheduled job completes, it sends results back through Telegram so you see them without checking a dashboard.

## Data Flow

```
User sends message in Telegram
    │
    ▼
bot.py receives Update via polling
    │
    ▼
Auth check: cached token for this user?
    │
    yes → use cached token from Redis (24h TTL)
    no  → admin-api via api-gateway: get_or_create_auth(telegram_id)
              │
              ▼
          Mints API token, caches in Redis with TTL
    │
    ▼
POST /api/chat to api-gateway :8056 with X-API-Key header
    │
    ▼
agent-api :8100 receives chat request, starts SSE stream
    │
    ▼
bot.py accumulates text_delta events, edits message every 1s
    │                          (progressive: user sees typing effect)
    ▼
stream ends (done event)
    │
    ▼
Final edit_message_text with complete response + stop button

---

Meeting commands (/join, /stop, /transcript):
    bot.py → api-gateway :8056 → meeting-api :8080

Session commands (/new, /sessions):
    bot.py → api-gateway :8056 → agent-api :8100

Error handling:
    403 from gateway → _invalidate_token() → retry once with fresh token
    API timeout      → error message to user
    Stream breaks    → partial response shown, error appended

Group chat:
    No @mention or reply-to-bot → ignored
    @mention → strip bot name, process normally
    Reply to bot message → process normally
```

## Code Ownership

```
services/telegram-bot/bot.py          → all Telegram integration, SSE streaming, commands, auth
services/telegram-bot/tests/          → 52 unit tests (auth, streaming, commands, concurrency, group chat)
services/telegram-bot/Dockerfile      → container build
services/telegram-bot/requirements.txt → Python dependencies
```

## What

Telegram interface for the Vexa Agent. Receives messages from Telegram users, forwards them to the Agent API, and streams responses back with progressive message editing. Also exposes an internal trigger API for scheduled messages.

### Commands

| Command | Description |
|---------|-------------|
| `/start` | Show bot info and user ID |
| `/reset` | Reset the chat session (keeps workspace files) |

Text messages are forwarded to the Agent API as chat turns. Responses stream back with a stop button for interruption.

## Internal Trigger API

A FastAPI server runs alongside the Telegram bot for programmatic message injection (used by the scheduler).

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/internal/trigger` | Send a message to a user's agent via Telegram |
| `GET` | `/health` | Health check |

## How

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | — | **Required.** Telegram Bot API token |
| `CHAT_API_URL` | `http://agent-api:8100` | Agent API base URL |
| `BOT_API_TOKEN` | — | API key for authenticating with the Agent API |
| `CHAT_DEFAULT_USER_ID` | — | Default Vexa user ID (all chats map to this user) |
| `CHAT_USER_MAP` | `{}` | JSON map of `{"telegram_chat_id": "vexa_user_id"}` |
| `TELEGRAM_BOT_PORT` | `8200` | Port for the internal trigger API |
| `LOG_LEVEL` | `INFO` | Log level |

### Run

```bash
cd services/telegram-bot
pip install -r requirements.txt
python bot.py
```

Requires the Agent API to be running. Set `TELEGRAM_BOT_TOKEN` to a valid bot token from [@BotFather](https://t.me/BotFather).

## Constraints

- telegram-bot is the ONLY Telegram integration point — no other service talks to Telegram API
- All meeting/agent operations go through api-gateway — never call agent-api or meeting-api directly
- Auth: bot calls admin-api (via gateway) for token, uses it as `X-API-Key` for subsequent requests
- Chat uses SSE streaming from agent-api `/api/chat` — no polling, no webhooks for responses
- Session state lives in agent-api + Redis — bot is stateless except cached auth token
- No Python imports from packages/ — standalone service with HTTP-only integration
- Progressive message editing (1s interval) — never send multiple messages for one response
- Trigger API (`/internal/trigger`) is internal only — not exposed through gateway
- State keyed by `(chat_id, user_id)` — concurrent users in the same group chat are isolated
- README.md MUST be updated when behavior changes

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` internal trigger API returns 200 | 15 | ceiling | untested | — | — | — |
| 2 | Bot starts polling with valid `TELEGRAM_BOT_TOKEN` | 20 | ceiling | untested | — | — | — |
| 3 | Text message forwarded to agent-api and SSE response streamed back | 25 | ceiling | untested | — | — | — |
| 4 | `POST /internal/trigger` sends message to user's agent | 15 | — | untested | — | — | — |
| 5 | Auth token cached in Redis and refreshed on 403 | 15 | — | untested | — | — | — |
| 6 | `CHAT_API_URL` (agent-api via gateway) reachable | 10 | ceiling | untested | — | — | — |

Confidence: 0 (untested)

## Known Issues

- No retry/backoff on API errors (single-shot with error message to user)
- Telegram transport layer untested (no TELEGRAM_BOT_TOKEN configured in dev/CI)
- Token refresh tested via unit test mocks, not E2E with real 403 scenario
- Group chat filtering tested via unit tests only (needs real Telegram group for E2E)
- Trigger API not tested E2E (scheduler → /internal/trigger → Telegram)
