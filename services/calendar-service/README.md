# Calendar Service

## Why

Users shouldn't have to manually send a bot to every meeting. Most meetings are already on their calendar with URLs attached. The calendar service watches for upcoming events and auto-schedules bots, turning Vexa from "tool you invoke" into "tool that acts on your behalf." Without it, every meeting requires a manual `POST /bots` call or a Telegram message.

## What

Syncs Google Calendar events and automatically schedules meeting bots to join upcoming calls. Runs a background sync loop that polls all connected users on a configurable interval.

## What

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/calendar/connect` | Trigger initial sync after OAuth connection |
| `GET` | `/calendar/status` | Check if a user has a calendar connected |
| `GET` | `/calendar/events` | List upcoming calendar events for a user |
| `PUT` | `/calendar/preferences` | Set auto-join and lead time preferences |
| `DELETE` | `/calendar/disconnect` | Remove OAuth tokens and stop syncing |
| `GET` | `/health` | Health check |

All endpoints accept `user_id` as a query parameter.

## How It Works

1. Users connect their Google Calendar via OAuth (refresh token stored in the `users.data` JSONB column).
2. A background loop syncs calendar events for all connected users at a regular interval.
3. For events with meeting URLs (Zoom, Teams, Meet), bots are scheduled to join automatically based on user preferences (auto-join enabled, lead time in minutes).

## How

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Log level |
| `SYNC_INTERVAL_SECONDS` | `300` | Seconds between calendar sync cycles |
| `DATABASE_URL` | — | PostgreSQL connection string (via admin-models/meeting-api) |

### Run

```bash
cd services/calendar-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8050 --reload
```

Requires PostgreSQL with the shared database schema initialized.

## DoD

| # | Check | Weight | Ceiling | Status | Evidence | Last checked | Tests |
|---|-------|--------|---------|--------|----------|--------------|-------|
| 1 | `GET /health` returns 200 | 15 | ceiling | untested | — | — | — |
| 2 | `POST /calendar/connect` triggers initial sync for user | 20 | ceiling | untested | — | — | — |
| 3 | `GET /calendar/events` returns upcoming events for connected user | 20 | — | untested | — | — | — |
| 4 | Background sync loop polls on `SYNC_INTERVAL_SECONDS` and schedules bots | 20 | ceiling | untested | — | — | — |
| 5 | `DATABASE_URL` set and PostgreSQL reachable | 15 | ceiling | untested | — | — | — |
| 6 | `DELETE /calendar/disconnect` removes OAuth tokens and stops syncing | 10 | — | untested | — | — | — |

Confidence: 0 (untested)
