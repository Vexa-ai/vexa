# User Settings API

These endpoints control per-user defaults and callbacks.

## GET /recording-config

Get your default recording configuration.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/recording-config"
```

## PUT /recording-config

Set your default recording configuration.

```bash
curl -X PUT "$API_BASE/recording-config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "enabled": true,
    "capture_modes": ["audio"]
  }'
```

## PUT /user/webhook

Set a webhook URL for events (for example, when a meeting completes processing).

Notes:

- The URL must be publicly reachable (private/internal URLs are rejected for SSRF prevention).
- If you set `webhook_secret`, Vexa will send `Authorization: Bearer <secret>` on webhook requests.

```bash
curl -X PUT "$API_BASE/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "webhook_url": "https://your-service.com/webhook",
    "webhook_secret": "optional-shared-secret"
  }'
```

