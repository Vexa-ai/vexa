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
    *   `platform`: (string, required) The meeting platform (e.g., "google_meet"). *Currently, only Google Meet is supported. Support for other platforms is coming soon.*
    *   `native_meeting_id`: (string, required) The unique identifier for the meeting.
    *   `language`: (string, optional) The desired transcription language code (e.g., "en", "es"). If omitted, the language spoken at the beginning of the meeting will be automatically detected once, and transcription will continue in that language (translating if necessary). To change the language mid-meeting, use the 'Update Bot Configuration' endpoint.
    *   `bot_name`: (string, optional) A custom name for the bot. This is the name the bot will use when appearing in the meeting.
*   **Response:** Returns details about the requested bot instance and meeting record.
*   **Note:** After a successful API response, it typically takes about 10 seconds for the bot to request entry into the meeting.
*   **Python Example:**
    ```python
    import requests
    import json

    BASE_URL = "https://gateway.dev.vexa.ai"
    API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

    HEADERS = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
    }

    meeting_platform = "google_meet"
    meeting_id = "abc-defg-hij" # Replace with a real meeting ID/URL part

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

### Get Real Time Meeting Transcript

*   **Endpoint:** `GET /transcripts/{platform}/{native_meeting_id}`
*   **Description:** Retrieves the meeting transcript. This provides **real-time** transcription data and can be called **during or after** the meeting has concluded.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting.
    *   `native_meeting_id`: (string) The unique identifier of the meeting.
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns the transcript data, typically including segments with speaker, timestamp, and text.
*   **Python Example:**
    ```python
    import requests
    import json

    BASE_URL = "https://gateway.dev.vexa.ai"
    API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

    HEADERS = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
    }

    meeting_platform = "google_meet"
    meeting_id = "abc-defg-hij" # Replace with a real meeting ID/URL part

    get_transcript_url = f"{BASE_URL}/transcripts/{meeting_platform}/{meeting_id}"
    
    response = requests.get(get_transcript_url, headers=HEADERS)
    
    print(response.json())
    ```

### Get Status of Running Bots

*   **Endpoint:** `GET /bots/status`
*   **Description:** Lists the bots currently running under your API key.
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Returns a list detailing the status of active bots.
*   **Python Example:**
    ```python
    import requests
    import json

    BASE_URL = "https://gateway.dev.vexa.ai"
    API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

    HEADERS = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
    }

    get_status_url = f"{BASE_URL}/bots/status"
    
    response = requests.get(get_status_url, headers=HEADERS)
    
    print(response.json())
    ```

### Update Bot Configuration

*   **Endpoint:** `PUT /bots/{platform}/{native_meeting_id}/config`
*   **Description:** Updates the configuration of an active bot (e.g., changing the language).
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting.
    *   `native_meeting_id`: (string) The identifier of the meeting with the active bot.
*   **Headers:**
    *   `Content-Type: application/json`
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Request Body:** A JSON object containing the configuration parameters to update (e.g., `{"language": "new_language_code"}`). The specific parameters accepted depend on the API implementation.
*   **Response:** Indicates whether the update request was accepted.
*   **Python Example:**
    ```python
    import requests
    import json

    BASE_URL = "https://gateway.dev.vexa.ai"
    API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

    HEADERS = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
    }

    meeting_platform = "google_meet"
    meeting_id = "abc-defg-hij" # Replace with a real meeting ID/URL part

    update_config_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}/config"
    update_payload = {
        "language": "es" # Example: change language to Spanish
    }
    
    response = requests.put(update_config_url, headers=HEADERS, json=update_payload)
    
    # print(f"Status Code: {response.status_code}")
    # Handle potential empty body or non-JSON response for PUT
    if response.content:
        try:
            print(response.json())
        except json.JSONDecodeError:
            print("Response (non-JSON):", response.text)
    else:
        print("Request accepted (No Content)") # Or just pass
    ```

### Stop a Bot

*   **Endpoint:** `DELETE /bots/{platform}/{native_meeting_id}`
*   **Description:** Removes an active bot from a meeting.
*   **Path Parameters:**
    *   `platform`: (string) The platform of the meeting.
    *   `native_meeting_id`: (string) The identifier of the meeting.
*   **Headers:**
    *   `X-API-Key: YOUR_API_KEY_HERE`
*   **Response:** Confirms the bot removal, potentially returning the final meeting record details.
*   **Python Example:**
    ```python
    import requests
    import json

    BASE_URL = "https://gateway.dev.vexa.ai"
    API_KEY = "YOUR_API_KEY_HERE" # Replace with your actual API key

    HEADERS = {
        "X-API-Key": API_KEY,
        "Content-Type": "application/json" # Include for POST/PUT, harmless for GET/DELETE
    }

    meeting_platform = "google_meet"
    meeting_id = "abc-defg-hij" # Replace with a real meeting ID/URL part

    stop_bot_url = f"{BASE_URL}/bots/{meeting_platform}/{meeting_id}"
    
    response = requests.delete(stop_bot_url, headers=HEADERS)
    
    # print(f"Status Code: {response.status_code}")
    # Handle potential empty body or non-JSON response for DELETE
    if response.content:
        try:
            print(response.json())
        except json.JSONDecodeError:
            print("Response (non-JSON):", response.text)
    else:
        print("Request successful (No Content)") # Or just pass
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

    BASE_URL = "https://gateway.dev.vexa.ai"
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

## Need Help?

Contact Vexa support via the designated channels if you encounter issues or have questions regarding API usage or API key provisioning.
