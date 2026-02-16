# Meetings API

Meetings are created/updated as bots run. You can list history, attach metadata (notes), and delete/anonymize artifacts.

## GET /meetings

List meetings for the authenticated user.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings"
```

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "meetings": [
    {
      "id": 16,
      "user_id": 1,
      "platform": "google_meet",
      "native_meeting_id": "abc-defg-hij",
      "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
      "status": "completed",
      "bot_container_id": null,
      "start_time": "2026-02-13T20:10:12Z",
      "end_time": "2026-02-13T20:44:51Z",
      "data": {
        "name": "Weekly Standup",
        "notes": "Discussed roadmap",
        "completion_reason": "stopped"
      },
      "created_at": "2026-02-13T20:10:00Z",
      "updated_at": "2026-02-13T20:44:51Z"
    }
  ]
}
```

</details>

## PATCH `/meetings/{platform}/{native_meeting_id}`

Update meeting metadata (stored in `meeting.data`), commonly used for:

- `name`
- `participants`
- `languages`
- `notes`

<Tabs>
  <Tab title="Google Meet">
```bash
curl -X PATCH "$API_BASE/meetings/google_meet/abc-defg-hij" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "data": {
      "name": "Weekly Standup",
      "notes": "Discussed roadmap"
    }
  }'
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -X PATCH "$API_BASE/meetings/teams/1234567890123" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "data": {
      "name": "Weekly Standup",
      "notes": "Discussed roadmap"
    }
  }'
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X PATCH "$API_BASE/meetings/zoom/12345678901" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "data": {
      "name": "Weekly Standup",
      "notes": "Discussed roadmap"
    }
  }'
```
  </Tab>
</Tabs>

Returns the updated meeting record.

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "id": 16,
  "user_id": 1,
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij",
  "constructed_meeting_url": "https://meet.google.com/abc-defg-hij",
  "status": "completed",
  "bot_container_id": null,
  "start_time": "2026-02-13T20:10:12Z",
  "end_time": "2026-02-13T20:44:51Z",
  "data": {
    "name": "Weekly Standup",
    "notes": "Discussed roadmap"
  },
  "created_at": "2026-02-13T20:10:00Z",
  "updated_at": "2026-02-13T20:44:51Z"
}
```

</details>

## DELETE `/meetings/{platform}/{native_meeting_id}`

Delete transcript data and recording artifacts (best-effort) and anonymize the meeting.

Important semantics:

- Only works for finalized meetings (`completed` or `failed`).
- Safe to retry: calling delete multiple times returns success; repeated calls are a no-op after the meeting is already anonymized.
- The meeting record remains for telemetry/usage tracking (with PII scrubbed).
- After deletion, the original `native_meeting_id` is cleared, so you cannot retry by `/{platform}/{native_meeting_id}` later.

<Tabs>
  <Tab title="Google Meet">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/google_meet/abc-defg-hij"
```
  </Tab>

  <Tab title="Microsoft Teams">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/teams/1234567890123"
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/zoom/12345678901"
```
  </Tab>
</Tabs>

If you want to delete just recordings (and keep transcript data), use:

- `DELETE /recordings/{recording_id}` (see [Recordings API](recordings.md))

Returns a human-readable confirmation message. This operation is safe to retry (idempotent): deleting an already-anonymized meeting returns success.

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "message": "Meeting google_meet/abc-defg-hij transcripts and recording artifacts deleted; meeting data anonymized"
}
```

</details>
