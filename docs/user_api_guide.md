# Vexa API Usage Guide

This document outlines how to interact with the Vexa API to manage meeting bots and retrieve transcripts during our free public beta phase.

## Authentication

All API requests described here require an API key for authentication.

### For Self-Hosted Deployments

If you're running Vexa on your own infrastructure, you need to create a user and generate an API token first:

1. **Create a user and token** - See the [Self-Hosted Management Guide](self-hosted-management.md) for detailed instructions
2. **Use the generated token** as your `X-API-Key` in all API requests

### For Hosted Service (vexa.ai)

* **Obtain your API Key:** Get your unique API key from [vexa.ai/dashboard/api-keys](https://vexa.ai/dashboard/api-keys)

### Using Your API Key

* **Include the Key in Requests:** Add the API key to the header of every request:
  ```
  X-API-Key: YOUR_API_KEY_HERE
  ```

## API Endpoints

Before integrating, skim:

- [`docs/concepts.md`](concepts.md): meeting/bot/session model, transcript timing semantics, recordings
- Platform notes:
  - [`docs/platforms/google-meet.md`](platforms/google-meet.md)
  - [`docs/platforms/microsoft-teams.md`](platforms/microsoft-teams.md)
  - [`docs/platforms/zoom.md`](platforms/zoom.md) and [`docs/zoom-app-setup.md`](zoom-app-setup.md)

### Request a Bot for a Meeting

* **Endpoint:** `POST /bots`
* **Description:** Asks the Vexa platform to add a transcription bot to a meeting.
* **Headers:**
  * `Content-Type: application/json`
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Request Body:** A JSON object specifying the meeting details. Common fields include:
  * `platform`: (string, required) The meeting platform. Supported values:
    * `"google_meet"` - Google Meet meetings
    * `"teams"` - Microsoft Teams meetings
    * `"zoom"` - Zoom meetings
  * `native_meeting_id`: (string, required) The unique identifier for the meeting. Format depends on platform:
    * **Google Meet**: Meeting code in format `xxx-xxxx-xxx` (e.g., "abc-defg-hij")
    * **Teams**: **Only the numeric meeting ID** (10-15 digits). Extract this from your Teams URL. For example, from `https://teams.live.com/meet/9366473044740?p=xxx`, use `"9366473044740"`.
    * **Zoom**: **Only the numeric meeting ID** (10-11 digits). Extract this from your Zoom URL. For example, from `https://us05web.zoom.us/j/89055866087?pwd=...`, use `"89055866087"`.
  * `passcode`: (string, required for Teams) Meeting passcode. **Required for Microsoft Teams meetings**. Extract this from the `?p=` parameter in your Teams URL. For example, from `https://teams.live.com/meet/9366473044740?p=waw4q9dPAvdIG3aknh`, use `"waw4q9dPAvdIG3aknh"`. Not used for Google Meet.
    * **Zoom**: Optional. If your Zoom URL includes `?pwd=...`, pass the value as `passcode`.
  * `language`: (string, optional) The desired transcription language code (e.g., "en", "es"). The language codes (currently 100) used in Vexa are based on the ISO 639-1 and ISO 639-3 standards. For more details, see the ISO 639 [specification on Wikipedia](https://en.wikipedia.org/wiki/ISO_639). If omitted, the language spoken at the beginning of the meeting will be automatically detected once, and transcription will continue in that language (translating if necessary). To change the language mid-meeting, use the 'Update Bot Configuration' endpoint.
  * `bot_name`: (string, optional) A custom name for the bot. This is the name the bot will use when appearing in the meeting.
  * `recording_enabled`: (boolean, optional) Per-meeting override to enable/disable recording persistence.
  * `transcribe_enabled`: (boolean, optional) Per-meeting override to enable/disable transcription processing.
  * `transcription_tier`: (string, optional) `"realtime"` (default) or `"deferred"` (lower priority / less real-time pressure).
  * `task`: (string, optional) `"transcribe"` (default) or `"translate"`.
  * `zoom_obf_token`: (string, optional) One-time Zoom OBF token. If omitted for Zoom meetings, the backend may mint one from the user's stored Zoom OAuth connection (if configured).
* **Response:** Returns details about the requested bot instance and meeting record.
* **Note:** After a successful API response, it typically takes about 10 seconds for the bot to request entry into the meeting.
* **Python Example (Google Meet):**
  ```python
  import requests
  import json

  BASE_URL = "https://api.cloud.vexa.ai"
  API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

  HEADERS = {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json"
  }

  # Google Meet example
  meeting_platform = "google_meet"
  meeting_id = "abc-defg-hij" # Replace with your meeting code from URL https://meet.google.com/abc-defg-hij

  request_bot_url = f"{BASE_URL}/bots"
  request_bot_payload = {
      "platform": meeting_platform,
      "native_meeting_id": meeting_id,
      "language": "en", # Optional: specify language
      "bot_name": "MyMeetingBot" # Optional: custom name
  }

  response = requests.post(request_bot_url, headers=HEADERS, json=request_bot_payload)

  print(response.json())
  ```
* **Python Example (Microsoft Teams):**
  ```python
  # Microsoft Teams example
  # From URL: https://teams.live.com/meet/9387167464734?p=qxJanYOcdjN4d6UlGa
  # Extract: meeting_id = "9387167464734" and passcode = "qxJanYOcdjN4d6UlGa"

  request_bot_payload = {
      "platform": "teams",
      "native_meeting_id": "9387167464734",  # Numeric meeting ID only
      "passcode": "qxJanYOcdjN4d6UlGa",      # Required passcode parameter
      "language": "en",
      "bot_name": "MyMeetingBot"
  }

  response = requests.post(request_bot_url, headers=HEADERS, json=request_bot_payload)
  print(response.json())
  ```
* **cURL Example (Google Meet):**
  ```bash
  curl -X POST \
    https://api.cloud.vexa.ai/bots \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "platform": "google_meet",
      "native_meeting_id": "abc-defg-hij",
      "language": "en",
      "bot_name": "MyMeetingBot"
    }'
  ```
* **cURL Example (Microsoft Teams):**
  ```bash
  # From URL: https://teams.live.com/meet/9387167464734?p=qxJanYOcdjN4d6UlGa
  # Extract meeting ID and passcode separately
  curl -X POST \
    https://api.cloud.vexa.ai/bots \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "platform": "teams",
      "native_meeting_id": "9387167464734",
      "passcode": "qxJanYOcdjN4d6UlGa",
      "language": "en",
      "bot_name": "MyMeetingBot"
    }'
  ```

* **cURL Example (Zoom):**
  ```bash
  # Caveat: Zoom Meeting SDK apps typically require Marketplace approval to join other users' meetings.
  # Before approval, expect you can reliably join only meetings created by you (the authorizing account).
  #
  # From URL: https://us05web.zoom.us/j/YOUR_MEETING_ID?pwd=YOUR_PWD
  # Extract meeting ID and optional passcode separately.
  curl -X POST \
    https://api.cloud.vexa.ai/bots \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "platform": "zoom",
      "native_meeting_id": "YOUR_MEETING_ID",
      "passcode": "YOUR_PWD",
      "recording_enabled": true,
      "transcribe_enabled": true,
      "transcription_tier": "realtime"
    }'
  ```

### Get Real Time Meeting Transcript

* **Endpoint:** `GET /transcripts/{platform}/{native_meeting_id}`
* **Description:** Retrieves the meeting transcript. This provides **real-time** transcription data and can be called **during or after** the meeting has concluded.
* **Note:** For live meetings, consider using WebSocket connections instead of frequent polling for better efficiency and lower latency. WebSocket connections provide efficient, low-latency transcript updates compared to polling REST endpoints, and avoid the overhead of repeated HTTP requests. See the WebSocket documentation for real-time transcript updates.
* **Path Parameters:**
  * `platform`: (string) The platform of the meeting (`google_meet`, `teams`, or `zoom`).
  * `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
    * **Google Meet**: The meeting code (e.g., "abc-defg-hij")
    * **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
    * **Zoom**: The numeric meeting ID only (10-11 digits), **not the full URL**
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Response:** Returns the transcript data (meeting + segments).
  * If you set meeting metadata via `PATCH /meetings/{platform}/{native_meeting_id}`, the transcript response also surfaces `notes` (from `meeting.data.notes`) for convenience.
  * If recording was enabled and captured, the response also includes a `recordings` array.
* **Python Example:**
  ```python
  # imports, HEADERS, meeting_id, meeting_platform as ABOVE

  get_transcript_url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
  response = requests.get(get_transcript_url, headers=HEADERS)
  print(response.json())
  ```
* **cURL Examples:**
  ```bash
  # Google Meet
  curl -X GET \
    https://api.cloud.vexa.ai/transcripts/google_meet/abc-defg-hij \
    -H 'X-API-Key: YOUR_API_KEY_HERE'

  # Microsoft Teams (use the numeric meeting ID, not the full URL)
  curl -X GET \
    https://api.cloud.vexa.ai/transcripts/teams/9387167464734 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### Access Recordings (Download/Playback)

If recording is enabled and a recording was captured, `GET /transcripts/{platform}/{native_meeting_id}` includes a `recordings` array. Each recording contains `media_files` (typically an `audio` WAV file).

Common flow:

1. Call `GET /transcripts/{platform}/{native_meeting_id}` and read:
   - `recordings[0].id` (recording ID)
   - `recordings[0].media_files[0].id` (media file ID)
2. Stream the bytes through the API (recommended for browser playback via same-origin proxy):
   - `GET /recordings/{recording_id}/media/{media_file_id}/raw`
3. Or request a presigned URL (object storage backends):
   - `GET /recordings/{recording_id}/media/{media_file_id}/download`

For storage and playback details (including `Range`/`206` seeking and `Content-Disposition: inline`), see [`docs/recording-storage.md`](recording-storage.md).

### Recordings API (List/Get/Delete)

Recordings can also be accessed directly via the Recordings API:

#### List recordings

* **Endpoint:** `GET /recordings`
* **Description:** List recordings for the authenticated user. Supports optional meeting filter.
* **Query Parameters:**
  * `meeting_id`: (int, optional) Internal meeting ID (as returned by `GET /meetings`)
  * `limit`: (int, optional) Default 50
  * `offset`: (int, optional) Default 0
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **cURL Example:**
  ```bash
  curl -X GET \
    https://api.cloud.vexa.ai/recordings?limit=50&offset=0 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

#### Get recording details

* **Endpoint:** `GET /recordings/{recording_id}`
* **Description:** Get a recording and its `media_files`.
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **cURL Example:**
  ```bash
  curl -X GET \
    https://api.cloud.vexa.ai/recordings/123456789 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

#### Delete a recording

* **Endpoint:** `DELETE /recordings/{recording_id}`
* **Description:** Deletes a recording, its media files from storage, and related database rows (best-effort storage cleanup).
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **cURL Example:**
  ```bash
  curl -X DELETE \
    https://api.cloud.vexa.ai/recordings/123456789 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### Recording Configuration (Per User)

You can enable/disable recording by default (and set capture modes) for your bots:

* **Endpoint:** `GET /recording-config`
* **Endpoint:** `PUT /recording-config`

Example:

```bash
curl -X PUT \
  https://api.cloud.vexa.ai/recording-config \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_API_KEY_HERE' \
  -d '{
    "enabled": true,
    "capture_modes": ["audio"]
  }'
```

### Get Status of Running Bots

* **Endpoint:** `GET /bots/status`
* **Description:** Lists the bots currently running under your API key.
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Response:** Returns a list detailing the status of active bots.
* **Python Example:**
  ```python
  # imports, HEADERS, meeting_id, meeting_platform as ABOVE

  get_status_url = f"{BASE_URL}/bots/status"
  response = requests.get(get_status_url, headers=HEADERS)
  print(response.json())
  ```
* **cURL Example:**
  ```bash
  curl -X GET \
    https://api.cloud.vexa.ai/bots/status \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### Update Bot Configuration

* **Endpoint:** `PUT /bots/{platform}/{native_meeting_id}/config`
* **Description:** Updates the configuration of an active bot (e.g., changing the language, for details on language codes see [request bot section](#request-a-bot-for-a-meeting)) 
* **Path Parameters:**
  * `platform`: (string) The platform of the meeting (`google_meet`, `teams`, or `zoom`).
  * `native_meeting_id`: (string) The identifier of the meeting with the active bot. **Use the exact same value you provided when requesting the bot**:
    * **Google Meet**: The meeting code (e.g., "abc-defg-hij")
    * **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
    * **Zoom**: The numeric meeting ID only (10-11 digits), **not the full URL**
* **Headers:**
  * `Content-Type: application/json`
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Request Body:** A JSON object containing the configuration parameters to update (currently supports `language` and `task`).
* **Response:** Indicates whether the update request was accepted.
* **Python Example:**
  ```python
  # imports, HEADERS, meeting_id, meeting_platform as ABOVE

  update_config_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
  update_payload = {
      "language": "es" # Example: change language to Spanish
  }
  response = requests.put(update_config_url, headers=HEADERS, json=update_payload)
  print(response.json())

  ```
* **cURL Examples:**
  ```bash
  # Google Meet
  curl -X PUT \
    https://api.cloud.vexa.ai/bots/google_meet/abc-defg-hij/config \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "language": "es"
    }'

  # Microsoft Teams (use numeric meeting ID only)
  curl -X PUT \
    https://api.cloud.vexa.ai/bots/teams/9387167464734/config \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "language": "es"
    }'
  ```

### Stop a Bot

* **Endpoint:** `DELETE /bots/{platform}/{native_meeting_id}`
* **Description:** Removes an active bot from a meeting.
* **Path Parameters:**
  * `platform`: (string) The platform of the meeting (`google_meet`, `teams`, or `zoom`).
  * `native_meeting_id`: (string) The identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
    * **Google Meet**: The meeting code (e.g., "abc-defg-hij")
    * **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
    * **Zoom**: The numeric meeting ID only (10-11 digits), **not the full URL**
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Response:** Confirms the bot removal, potentially returning the final meeting record details.
* **Python Example:**
  ```python
     # imports, HEADERS, meeting_id, meeting_platform as ABOVE
  stop_bot_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
  response = requests.delete(stop_bot_url, headers=HEADERS)
  print(response.json())
  ```
* **cURL Examples:**
  ```bash
  # Google Meet
  curl -X DELETE \
    https://api.cloud.vexa.ai/bots/google_meet/abc-defg-hij \
    -H 'X-API-Key: YOUR_API_KEY_HERE'

  # Microsoft Teams (use numeric meeting ID only)
  curl -X DELETE \
    https://api.cloud.vexa.ai/bots/teams/9387167464734 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### List Your Meetings

* **Endpoint:** `GET /meetings`
* **Description:** Retrieves a history of meetings associated with your API key.
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Response:** Returns a list of meeting records.
* **Python Example:**
  ```python
  import requests
  import json

  BASE_URL = "https://api.cloud.vexa.ai"
  API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

  HEADERS = {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
  }

  list_meetings_url = f"{BASE_URL}/meetings"

  response = requests.get(list_meetings_url, headers=HEADERS)

  # print(f"Status Code: {response.status_code}")
  print(response.json())
  ```
* **cURL Example:**
  ```bash
  curl -X GET \
    https://api.cloud.vexa.ai/meetings \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### Update Meeting Data

* **Endpoint:** `PATCH /meetings/{platform}/{native_meeting_id}`
* **Description:** Updates meeting metadata such as name, participants, languages, and notes. Only these specific fields can be updated.
* **Path Parameters:**
  * `platform`: (string) The platform of the meeting (`google_meet`, `teams`, or `zoom`).
  * `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
    * **Google Meet**: The meeting code (e.g., "abc-defg-hij")
    * **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
    * **Zoom**: The numeric meeting ID only (10-11 digits), **not the full URL**
* **Headers:**
  * `Content-Type: application/json`
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Request Body:** A JSON object containing the meeting data to update:
  * `data`: (object, required) Container for meeting metadata
    * `name`: (string, optional) Meeting name/title
    * `participants`: (array, optional) List of participant names
    * `languages`: (array, optional) List of language codes (for details on language codes see [request bot section](#request-a-bot-for-a-meeting)) detected/used in the meeting
    * `notes`: (string, optional) Meeting notes or description
* **Response:** Returns the updated meeting record.
* **Python Example:**
  ```python
  # imports, HEADERS, meeting_id, meeting_platform as ABOVE

  update_meeting_url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
  update_payload = {
      "data": {
          "name": "Weekly Team Standup",
          "participants": ["Alice", "Bob", "Charlie"],
          "languages": ["en"],
          "notes": "Discussed Q4 roadmap and sprint planning"
      }
  }

  response = requests.patch(update_meeting_url, headers=HEADERS, json=update_payload)
  print(response.json())
  ```
* **cURL Examples:**
  ```bash
  # Google Meet
  curl -X PATCH \
    https://api.cloud.vexa.ai/meetings/google_meet/abc-defg-hij \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "data": {
        "name": "Weekly Team Standup",
        "participants": ["Alice", "Bob", "Charlie"],
        "languages": ["en"],
        "notes": "Discussed Q4 roadmap and sprint planning"
      }
    }'

  # Microsoft Teams (use numeric meeting ID only)
  curl -X PATCH \
    https://api.cloud.vexa.ai/meetings/teams/9387167464734 \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "data": {
        "name": "Weekly Team Standup",
        "participants": ["Alice", "Bob", "Charlie"],
        "languages": ["en"],
        "notes": "Discussed Q4 roadmap and sprint planning"
      }
    }'
  ```

### Delete Meeting Transcripts and Anonymize Data

* **Endpoint:** `DELETE /meetings/{platform}/{native_meeting_id}`
* **Description:** Purges transcripts and recording artifacts (if present) and anonymizes meeting data for finalized meetings. Only works for meetings in `completed` or `failed` states. Deletes transcript segments but preserves meeting and session records for telemetry purposes. Scrubs PII while keeping status transitions and completion reasons.
* **Path Parameters:**
  * `platform`: (string) The platform of the meeting (`google_meet`, `teams`, or `zoom`).
  * `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
    * **Google Meet**: The meeting code (e.g., "abc-defg-hij")
    * **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
    * **Zoom**: The numeric meeting ID only (10-11 digits), **not the full URL**
* **Headers:**
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Response:** Returns a confirmation message.
* **Notes:**
  * Deletion is **idempotent**: if the meeting is already anonymized (`data.redacted=true`), the endpoint returns success.
  * After deletion, the meeting is anonymized and its `native_meeting_id` is cleared, so the same `{platform}/{native_meeting_id}` cannot be used to retry cleanup later. Ensure your delete request succeeds before relying on it for artifact cleanup.
* **Error Responses:**
  * `404 Not Found`: Meeting not found.
  * `409 Conflict`: Meeting not finalized (not in completed or failed state).
* **Python Example:**
  ```python
  # imports, HEADERS, meeting_id, meeting_platform as ABOVE

  delete_meeting_url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
  response = requests.delete(delete_meeting_url, headers=HEADERS)
  print(response.json())
  ```
* **cURL Examples:**
  ```bash
  # Google Meet
  curl -X DELETE \
    https://api.cloud.vexa.ai/meetings/google_meet/abc-defg-hij \
    -H 'X-API-Key: YOUR_API_KEY_HERE'

  # Microsoft Teams (use numeric meeting ID only)
  curl -X DELETE \
    https://api.cloud.vexa.ai/meetings/teams/9387167464734 \
    -H 'X-API-Key: YOUR_API_KEY_HERE'
  ```

### Set User Webhook URL

* **Endpoint:** `PUT /user/webhook`
* **Description:** Sets a webhook URL for the authenticated user. When events occur (e.g., a meeting finishes processing), a POST request with the meeting data will be sent to this URL. Webhook URLs must target public internet addresses; internal or private network URLs are rejected for security (SSRF prevention).
* **Headers:**
  * `Content-Type: application/json`
  * `X-API-Key: YOUR_API_KEY_HERE`
* **Request Body:** A JSON object containing:
  * `webhook_url`: (string, required) The full URL to which Vexa should send webhook notifications. Must use `http://` or `https://` and cannot target localhost, private IPs, or internal hostnames.
  * `webhook_secret`: (string, optional) If provided, Vexa adds `Authorization: Bearer <secret>` to outgoing webhook requests. Useful for authenticating webhooks to services like OpenClaw. The secret is never returned in API responses. Omit this field to leave an existing secret unchanged when updating only the URL (backward compatible).
* **Response:** Returns the updated user record. The `webhook_secret` (if set) is never included in the response.
* **Python Example:**
  ```python
  # imports, HEADERS from previous examples

  set_webhook_url = f"{BASE_URL}/user/webhook"
  webhook_payload = {
      "webhook_url": "https://your-service.com/webhook-receiver",
      "webhook_secret": "optional-shared-secret-for-auth-header"  # optional
  }

  response = requests.put(set_webhook_url, headers=HEADERS, json=webhook_payload)
  print(response.json())
  ```
* **cURL Example:**
  ```bash
  curl -X PUT \
    https://api.cloud.vexa.ai/user/webhook \
    -H 'Content-Type: application/json' \
    -H 'X-API-Key: YOUR_API_KEY_HERE' \
    -d '{
      "webhook_url": "https://your-service.com/webhook-receiver",
      "webhook_secret": "optional-shared-secret-for-auth-header"
    }'
  ```

---

### Voice Agent (Meeting Interaction)

When a bot is requested with `voice_agent_enabled: true`, additional endpoints become available to control the bot during a live meeting. For full details, see the [Voice Agent Guide](voice-agent.md).

#### Speak (Text-to-Speech)

* **Endpoint:** `POST /bots/{platform}/{native_meeting_id}/speak`
* **Description:** Make the bot speak in the meeting via TTS or pre-rendered audio.
* **Request Body:**
  ```json
  {"text": "Hello everyone", "provider": "openai", "voice": "alloy"}
  ```
* **Interrupt:** `DELETE /bots/{platform}/{native_meeting_id}/speak`

#### Chat

* **Send:** `POST /bots/{platform}/{native_meeting_id}/chat`
  ```json
  {"text": "Here is the summary."}
  ```
* **Read:** `GET /bots/{platform}/{native_meeting_id}/chat` — returns `{"messages": [...]}`

#### Screen Share

* **Show content:** `POST /bots/{platform}/{native_meeting_id}/screen`
  ```json
  {"type": "image", "url": "https://example.com/chart.png"}
  ```
* **Stop sharing:** `DELETE /bots/{platform}/{native_meeting_id}/screen`

---

## Need Help?

Contact Vexa support via the designated channels if you encounter issues or have questions regarding API usage or API key provisioning.
