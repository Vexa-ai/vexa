# User Settings API

These endpoints control per-user defaults and callbacks.

## GET /recording-config

Get your default recording configuration.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/recording-config"
```

### Response (200)

<details>
  <summary>Show response JSON</summary>

```json
{
  "enabled": true,
  "capture_modes": ["audio"]
}
```

</details>

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

### Response (200)

Returns the updated configuration.

<details>
  <summary>Show response JSON</summary>

```json
{
  "enabled": true,
  "capture_modes": ["audio"]
}
```

</details>

## PUT /user/webhook

Set a webhook URL for events (for example, when a meeting completes processing).

See also:

- [`docs/webhooks.md`](../webhooks.md)
- Local dev: [`docs/local-webhook-development.md`](../local-webhook-development.md)

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

### Response (200)

Returns the updated user record. For security, `webhook_secret` is never returned.

<details>
  <summary>Show response JSON</summary>

```json
{
  "id": 1,
  "email": "you@company.com",
  "name": "Your Name",
  "image_url": null,
  "max_concurrent_bots": 2,
  "data": {
    "webhook_url": "https://your-service.com/webhook"
  },
  "created_at": "2026-02-01T10:00:00Z"
}
```

</details>
