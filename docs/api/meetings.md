# Meetings API

Meetings are created/updated as bots run. You can list history, attach metadata (notes), and delete/anonymize artifacts.

## GET /meetings

List meetings for the authenticated user.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings"
```

### Response (200)

<details>
  <summary>Show response JSON</summary>

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
curl -X PATCH "$API_BASE/meetings/teams/9321836506982" \
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
curl -X PATCH "$API_BASE/meetings/zoom/89055866087" \
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

### Response (200)

Returns the updated meeting record.

<details>
  <summary>Show response JSON</summary>

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
- Deletion is **idempotent** (already-redacted meetings return success).
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
  "$API_BASE/meetings/teams/9321836506982"
```
  </Tab>

  <Tab title="Zoom">
```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/meetings/zoom/89055866087"
```
  </Tab>
</Tabs>

If you want to delete just recordings (and keep transcript data), use:

- `DELETE /recordings/{recording_id}` (see [`docs/api/recordings.md`](recordings.md))

### Response (200)

Returns a human-readable confirmation message. This operation is idempotent: deleting an already-redacted meeting returns success.

<details>
  <summary>Show response JSON</summary>

```json
{
  "message": "Meeting google_meet/abc-defg-hij transcripts and recording artifacts deleted; meeting data anonymized"
}
```

</details>
