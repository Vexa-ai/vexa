# Vexa API Usage Guide

**Status: Free Public Beta**

This document outlines how to interact with the Vexa API to manage meeting bots and retrieve transcripts during our free public beta phase.

## Authentication

All API requests described here require an API key for authentication.

*   **Obtain your API Key:** Request your unique `X-API-Key`.
*   **Include the Key in Requests:** Add the API key to the header of every request:
    ```
    X-API-Key: YOUR_API_KEY_HERE
    ```

## Concurrent Bot Limit

The default limit is **one (1) concurrently running bot** per user account. If you require a higher limit during the beta, please contact the Vexa team on our Discord channel to request an increase.

## API Endpoints

### Request a Bot for a Meeting

*   **Endpoint:** `POST /bots`
*   **Description:** Asks the Vexa platform to add a transcription bot to a meeting.
*   **Headers:**
    *   `Content-Type: application/json`
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Request Body:** A JSON object specifying the meeting details. Common fields include:
    *   `platform`: (string, required) The meeting platform. Supported values:
        *   `"google_meet"` - Google Meet meetings
        *   `"teams"` - Microsoft Teams meetings
    *   `native_meeting_id`: (string, required) The unique identifier for the meeting. Format depends on platform:
        *   **Google Meet**: Meeting code in format `xxx-xxxx-xxx` (e.g., "abc-defg-hij")
        *   **Teams**: **Only the numeric meeting ID** (10-15 digits). Extract this from your Teams URL. For example, from `https://teams.live.com/meet/9366473044740?p=xxx`, use `"9366473044740"`.
    *   `passcode`: (string, required for Teams) Meeting passcode. **Required for Microsoft Teams meetings**. Extract this from the `?p=` parameter in your Teams URL. For example, from `https://teams.live.com/meet/9366473044740?p=waw4q9dPAvdIG3aknh`, use `"waw4q9dPAvdIG3aknh"`. Not used for Google Meet.
    *   `language`: (string, optional) The desired transcription language code (e.g., "en", "es"). If omitted, the language spoken at the beginning of the meeting will be automatically detected once, and transcription will continue in that language (translating if necessary). To change the language mid-meeting, use the 'Update Bot Configuration' endpoint.
    *   `bot_name`: (string, optional) A custom name for the bot. This is the name the bot will use when appearing in the meeting.
*   **Response:** Returns details about the requested bot instance and meeting record.
*   **Note:** After a successful API response, it typically takes about 10 seconds for the bot to request entry into the meeting.
*   **Python Example (Google Meet):**
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
*   **Python Example (Microsoft Teams):**
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
*   **cURL Example (Google Meet):**
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
*   **cURL Example (Microsoft Teams):**
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

### Get Real Time Meeting Transcript

*   **Endpoint:** `GET /transcripts/{platform}/{native_meeting_id}`
*   **Description:** Retrieves the meeting transcript. This provides **real-time** transcription data and can be called **during or after** the meeting has concluded.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting (`google_meet` or `teams`).
    *   `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
        *   **Google Meet**: The meeting code (e.g., "abc-defg-hij")
        *   **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns the transcript data, typically including segments with speaker, timestamp, and text.
*   **Python Example:**
    ```python
    # imports, HEADERS, meeting_id, meeting_platform as ABOVE
    
    get_transcript_url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
    response = requests.get(get_transcript_url, headers=HEADERS)
    print(response.json())
    ```
*   **cURL Examples:**
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

### Get Status of Running Bots

*   **Endpoint:** `GET /bots/status`
*   **Description:** Lists the bots currently running under your API key.
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns a list detailing the status of active bots.
*   **Python Example:**
    ```python
    # imports, HEADERS, meeting_id, meeting_platform as ABOVE
    
    get_status_url = f"{BASE_URL}/bots/status"
    response = requests.get(get_status_url, headers=HEADERS)
    print(response.json())
    ```
*   **cURL Example:**
    ```bash
    curl -X GET \
      https://api.cloud.vexa.ai/bots/status \
      -H 'X-API-Key: YOUR_API_KEY_HERE'
    ```

### Update Bot Configuration

*   **Endpoint:** `PUT /bots/{platform}/{native_meeting_id}/config`
*   **Description:** Updates the configuration of an active bot (e.g., changing the language).
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting (`google_meet` or `teams`).
    *   `native_meeting_id`: (string) The identifier of the meeting with the active bot. **Use the exact same value you provided when requesting the bot**:
        *   **Google Meet**: The meeting code (e.g., "abc-defg-hij")
        *   **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
*   **Headers:**
    *   `Content-Type: application/json`
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Request Body:** A JSON object containing the configuration parameters to update (e.g., `{"language": "new_language_code"}`). The specific parameters accepted depend on the API implementation.
*   **Response:** Indicates whether the update request was accepted.
*   **Python Example:**
    ```python
    # imports, HEADERS, meeting_id, meeting_platform as ABOVE

    update_config_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
    update_payload = {
        "language": "es" # Example: change language to Spanish
    }
    response = requests.put(update_config_url, headers=HEADERS, json=update_payload)
    print(response.json())

    ```
*   **cURL Examples:**
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

*   **Endpoint:** `DELETE /bots/{platform}/{native_meeting_id}`
*   **Description:** Removes an active bot from a meeting.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting (`google_meet` or `teams`).
    *   `native_meeting_id`: (string) The identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
        *   **Google Meet**: The meeting code (e.g., "abc-defg-hij")
        *   **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Confirms the bot removal, potentially returning the final meeting record details.
*   **Python Example:**
    ```python
       # imports, HEADERS, meeting_id, meeting_platform as ABOVE
    stop_bot_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
    response = requests.delete(stop_bot_url, headers=HEADERS)
    print(response.json())
    ```
*   **cURL Examples:**
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

*   **Endpoint:** `GET /meetings`
*   **Description:** Retrieves a history of meetings associated with your API key.
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns a list of meeting records.
*   **Python Example:**
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
*   **cURL Example:**
    ```bash
    curl -X GET \
      https://api.cloud.vexa.ai/meetings \
      -H 'X-API-Key: YOUR_API_KEY_HERE'
    ```

### Update Meeting Data

*   **Endpoint:** `PATCH /meetings/{platform}/{native_meeting_id}`
*   **Description:** Updates meeting metadata such as name, participants, languages, and notes. Only these specific fields can be updated.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting (`google_meet` or `teams`).
    *   `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
        *   **Google Meet**: The meeting code (e.g., "abc-defg-hij")
        *   **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
*   **Headers:**
    *   `Content-Type: application/json`
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Request Body:** A JSON object containing the meeting data to update:
    *   `data`: (object, required) Container for meeting metadata
        *   `name`: (string, optional) Meeting name/title
        *   `participants`: (array, optional) List of participant names
        *   `languages`: (array, optional) List of language codes detected/used in the meeting
        *   `notes`: (string, optional) Meeting notes or description
*   **Response:** Returns the updated meeting record.
*   **Python Example:**
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
*   **cURL Examples:**
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

*   **Endpoint:** `DELETE /meetings/{platform}/{native_meeting_id}`
*   **Description:** Purges transcripts and anonymizes meeting data for finalized meetings. Only works for meetings in `completed` or `failed` states. Deletes all transcripts but preserves meeting and session records for telemetry purposes. Scrubs PII while keeping status transitions and completion reasons.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting (`google_meet` or `teams`).
    *   `native_meeting_id`: (string) The unique identifier of the meeting. **Use the exact same value you provided when requesting the bot**:
        *   **Google Meet**: The meeting code (e.g., "abc-defg-hij")
        *   **Teams**: The numeric meeting ID only (e.g., "9387167464734"), **not the full URL**
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns a confirmation message.
*   **Error Responses:**
    *   `404 Not Found`: Meeting not found.
    *   `409 Conflict`: Meeting not finalized (not in completed or failed state).
*   **Python Example:**
    ```python
    # imports, HEADERS, meeting_id, meeting_platform as ABOVE
    
    delete_meeting_url = f"{BASE_URL}/meetings/{meeting_platform}/{meeting_id}"
    response = requests.delete(delete_meeting_url, headers=HEADERS)
    print(response.json())
    ```
*   **cURL Examples:**
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

*   **Endpoint:** `PUT /user/webhook`
*   **Description:** Sets a webhook URL for the authenticated user. When events occur (e.g., a meeting finishes processing), a POST request with the meeting data will be sent to this URL.
*   **Headers:**
    *   `Content-Type: application/json`
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Request Body:** A JSON object containing the webhook URL:
    *   `webhook_url`: (string, required) The full URL to which Vexa should send webhook notifications.
*   **Response:** Returns the updated user record.
*   **Python Example:**
    ```python
    # imports, HEADERS from previous examples
    
    set_webhook_url = f"{BASE_URL}/user/webhook"
    webhook_payload = {
        "webhook_url": "https://your-service.com/webhook-receiver"
    }
    
    response = requests.put(set_webhook_url, headers=HEADERS, json=webhook_payload)
    print(response.json())
    ```
*   **cURL Example:**
    ```bash
    curl -X PUT \
      https://api.cloud.vexa.ai/user/webhook \
      -H 'Content-Type: application/json' \
      -H 'X-API-Key: YOUR_API_KEY_HERE' \
      -d '{
        "webhook_url": "https://your-service.com/webhook-receiver"
      }'
    ```

## Need Help?

Contact Vexa support via the designated channels if you encounter issues or have questions regarding API usage or API key provisioning.
