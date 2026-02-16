# Recordings API

Recordings are created only if recording is enabled **and** audio capture succeeds.

If a recording exists, it will also be returned by:

- `GET /transcripts/{platform}/{native_meeting_id}` → `recordings[]`

## GET /recordings

List recordings for the authenticated user.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/recordings?limit=50&offset=0"
```

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "recordings": [
    {
      "id": 906238426347,
      "meeting_id": 16,
      "user_id": 1,
      "session_uid": "d6e337d6-92cd-452f-b003-23c5498091ef",
      "source": "bot",
      "status": "completed",
      "error_message": null,
      "created_at": "2026-02-13T20:10:20Z",
      "completed_at": "2026-02-13T20:44:55Z",
      "media_files": [
        {
          "id": 906238426348,
          "type": "audio",
          "format": "wav",
          "storage_backend": "s3",
          "file_size_bytes": 1234567,
          "duration_seconds": 2079.2,
          "metadata": {
            "sample_rate": 16000
          },
          "created_at": "2026-02-13T20:44:55Z"
        }
      ]
    }
  ]
}
```

</details>

## GET /recordings/{recording_id}

Get a recording and its `media_files`.

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/recordings/123456789"
```

Same shape as an item in `GET /recordings`:

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "id": 906238426347,
  "meeting_id": 16,
  "user_id": 1,
  "session_uid": "d6e337d6-92cd-452f-b003-23c5498091ef",
  "source": "bot",
  "status": "completed",
  "error_message": null,
  "created_at": "2026-02-13T20:10:20Z",
  "completed_at": "2026-02-13T20:44:55Z",
  "media_files": [
    {
      "id": 906238426348,
      "type": "audio",
      "format": "wav",
      "storage_backend": "s3",
      "file_size_bytes": 1234567,
      "duration_seconds": 2079.2,
      "metadata": {
        "sample_rate": 16000
      },
      "created_at": "2026-02-13T20:44:55Z"
    }
  ]
}
```

</details>

## DELETE /recordings/{recording_id}

Delete a recording, its media files from storage, and related DB rows (best-effort storage cleanup).

```bash
curl -X DELETE \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/recordings/123456789"
```

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "status": "deleted",
  "recording_id": 906238426347
}
```

</details>

## GET /recordings/{recording_id}/media/{media_file_id}/raw

Authenticated byte streaming (best for browser playback via same-origin proxy).

- Returns `Content-Disposition: inline`
- Supports `Range` requests (`206`) for seeking in `<audio>`

```bash
curl -L \
  -H "X-API-Key: $API_KEY" \
  "$API_BASE/recordings/123456789/media/987654321/raw" \
  -o audio.wav
```

### Response (200 / 206)

Returns binary bytes (for example, `audio/wav`). Seeking uses `Range` with `206 Partial Content`.

## GET /recordings/{recording_id}/media/{media_file_id}/download

Returns a presigned URL for object storage backends (S3 / MinIO compatible).

```bash
curl -H "X-API-Key: $API_KEY" \
  "$API_BASE/recordings/123456789/media/987654321/download"
```

<details>
  <summary><strong>Response (200)</strong></summary>

```json
{
  "download_url": "https://s3.example.com/vexa-recordings/recordings/1/906238426347/d6e337d6-92cd-452f-b003-23c5498091ef.wav?X-Amz-Algorithm=AWS4-HMAC-SHA256&...",
  "filename": "906238426347_audio.wav",
  "content_type": "audio/wav",
  "file_size_bytes": 1234567
}
```

</details>

Storage configuration details:

- [Recording storage](../recording-storage.md)
