# Vexa API Gateway Documentation

**Version:** 1.2.0  
**OAS3** | [/openapi.json](http://localhost:18056/openapi.json)

## Errors

**Hide**

**Resolver error at paths./bots.post.requestBody.content.application/json.schema.properties.platform.$ref**  
Could not resolve reference: Could not resolve pointer: /definitions/Platform does not exist in document

---

## Overview

**Main entry point for the Vexa platform APIs.**

Provides access to:

- Bot Management (Starting/Stopping transcription bots)
- Transcription Retrieval
- User & Token Administration (Admin only)

## Authentication

Two types of API keys are used:

1. **`X-API-Key`**: Required for all regular client operations (e.g., managing bots, getting transcripts). Obtain your key from an administrator.
2. **`X-Admin-API-Key`**: Required _only_ for administrative endpoints (prefixed with `/admin`). This key is configured server-side.

Include the appropriate header in your requests.

**Support:**

- [Vexa Support - Website](mailto:support@vexa.com)
- Send email to Vexa Support
- **License:** Proprietary

---

## API Endpoints

### General

#### GET `/`

**API Gateway Root**

Provides a welcome message for the Vexa API Gateway.

**Parameters:** No parameters

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`

**Curl Command:**

```bash
curl -X GET "http://localhost:18056/"
```

---

### Bot Management

#### POST `/bots`

**Request a new bot to join a meeting**

Creates a new meeting record and launches a bot instance based on platform and native meeting ID.

**Parameters:** No parameters

**Request body:** `application/json`  
Specify the meeting platform, native ID, and optional bot name.

**Example Value:**

```json
{
  "platform": "string",
  "native_meeting_id": "string",
  "bot_name": "string",
  "language": "string",
  "task": "string"
}
```

**Responses:**

- **201** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`

**Curl Command:**

```bash
curl -X POST "http://localhost:18056/bots" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV" \
  -d '{
    "platform": "google_meet",
    "native_meeting_id": "abc-defg-hij",
    "bot_name": "TranscriptBot",
    "language": "en",
    "task": "transcribe"
  }'
```

#### DELETE `/bots/{platform}/{native_meeting_id}`

**Stop a bot for a specific meeting**

Stops the bot container associated with the specified platform and native meeting ID. Requires ownership via API key.

**Parameters:**

- `platform` _(string, path, required)_
  - **Available values:** `google_meet`, `zoom`, `teams`
  - **Default:** `google_meet`
- `native_meeting_id` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "id": 0,
      "user_id": 0,
      "platform": "google_meet",
      "native_meeting_id": "string",
      "constructed_meeting_url": "string",
      "status": "string",
      "bot_container_id": "string",
      "start_time": "2025-07-07T10:08:16.677Z",
      "end_time": "2025-07-07T10:08:16.677Z",
      "data": {},
      "created_at": "2025-07-07T10:08:16.677Z",
      "updated_at": "2025-07-07T10:08:16.677Z"
    }
    ```
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X DELETE "http://localhost:18056/bots/google_meet/abc-defg-hij" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV"
```

#### PUT `/bots/{platform}/{native_meeting_id}/config`

**Update configuration for an active bot**

Updates the language and/or task for an active bot. Sends command via Bot Manager.

**Parameters:**

- `platform` _(string, path, required)_
  - **Available values:** `google_meet`, `zoom`, `teams`
  - **Default:** `google_meet`
- `native_meeting_id` _(string, path, required)_

**Responses:**

- **202** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X PUT "http://localhost:18056/bots/google_meet/abc-defg-hij/config" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV" \
  -d '{
    "language": "es",
    "task": "translate"
  }'
```

#### GET `/bots/status`

**Get status of running bots for the user**

Retrieves a list of currently running bot containers associated with the authenticated user.

**Parameters:** No parameters

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "running_bots": [
        {
          "container_id": "string",
          "container_name": "string",
          "platform": "string",
          "native_meeting_id": "string",
          "status": "string",
          "created_at": "string",
          "labels": {
            "additionalProp1": "string",
            "additionalProp2": "string",
            "additionalProp3": "string"
          },
          "meeting_id_from_name": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X GET "http://localhost:18056/bots/status" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV"
```

---

### Transcriptions

#### GET `/meetings`

**Get list of user's meetings**

Returns a list of all meetings initiated by the user associated with the API key.

**Parameters:** No parameters

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "meetings": [
        {
          "id": 0,
          "user_id": 0,
          "platform": "google_meet",
          "native_meeting_id": "string",
          "constructed_meeting_url": "string",
          "status": "string",
          "bot_container_id": "string",
          "start_time": "2025-07-07T10:08:16.682Z",
          "end_time": "2025-07-07T10:08:16.682Z",
          "data": {},
          "created_at": "2025-07-07T10:08:16.682Z",
          "updated_at": "2025-07-07T10:08:16.682Z"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X GET "http://localhost:18056/meetings" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV"
```

#### GET `/transcripts/{platform}/{native_meeting_id}`

**Get transcript for a specific meeting**

Retrieves the transcript segments for a meeting specified by its platform and native ID.

**Parameters:**

- `platform` _(string, path, required)_
  - **Available values:** `google_meet`, `zoom`, `teams`
  - **Default:** `google_meet`
- `native_meeting_id` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "id": 0,
      "platform": "google_meet",
      "native_meeting_id": "string",
      "constructed_meeting_url": "string",
      "status": "string",
      "start_time": "2025-07-07T10:08:16.684Z",
      "end_time": "2025-07-07T10:08:16.684Z",
      "segments": [
        {
          "start": 0,
          "end": 0,
          "text": "string",
          "language": "string",
          "created_at": "2025-07-07T10:08:16.684Z",
          "speaker": "string",
          "absolute_start_time": "2025-07-07T10:08:16.684Z",
          "absolute_end_time": "2025-07-07T10:08:16.684Z"
        }
      ]
    }
    ```
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X GET "http://localhost:18056/transcripts/google_meet/abc-defg-hij" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV"
```

#### DELETE `/meetings/{platform}/{native_meeting_id}`

**Delete meeting and its transcripts**

Deletes a specific meeting and all its associated transcripts. This action cannot be undone.

**Parameters:**

- `platform` _(string, path, required)_
  - **Available values:** `google_meet`, `zoom`, `teams`
  - **Default:** `google_meet`
- `native_meeting_id` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X DELETE "http://localhost:18056/meetings/google_meet/abc-defg-hij" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV"
```

#### PATCH `/meetings/{platform}/{native_meeting_id}`

**Update meeting data**

Updates meeting metadata. Only name, participants, languages, and notes can be updated.

**Parameters:**

- `platform` _(string, path, required)_
  - **Available values:** `google_meet`, `zoom`, `teams`
  - **Default:** `google_meet`
- `native_meeting_id` _(string, path, required)_

**Request body:** `application/json`  
Meeting data to update (name, participants, languages, notes only)

**Example Value:**

```json
{
  "data": {
    "name": "string",
    "participants": ["string"],
    "languages": ["string"],
    "notes": "string"
  }
}
```

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "id": 0,
      "user_id": 0,
      "platform": "google_meet",
      "native_meeting_id": "string",
      "constructed_meeting_url": "string",
      "status": "string",
      "bot_container_id": "string",
      "start_time": "2025-07-07T10:08:16.689Z",
      "end_time": "2025-07-07T10:08:16.689Z",
      "data": {},
      "created_at": "2025-07-07T10:08:16.689Z",
      "updated_at": "2025-07-07T10:08:16.689Z"
    }
    ```
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X PATCH "http://localhost:18056/meetings/google_meet/abc-defg-hij" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV" \
  -d '{
    "data": {
      "name": "Weekly Team Meeting",
      "participants": ["Alice", "Bob", "Charlie"],
      "languages": ["en", "es"],
      "notes": "Important meeting notes"
    }
  }'
```

---

### User

#### PUT `/user/webhook`

**Set user webhook URL**

Sets a webhook URL for the authenticated user to receive notifications.

**Parameters:** No parameters

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`

**Curl Command:**

```bash
curl -X PUT "http://localhost:18056/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV" \
  -d '{
    "webhook_url": "https://your-endpoint.com/api/vexa-webhook"
  }'
```

---

### Administration

#### GET `/admin/{path}`

**Forward admin requests**

Forwards requests prefixed with /admin to the Admin API service. Requires X-Admin-API-Key.

**Parameters:**

- `path` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X GET "http://localhost:18056/admin/users" \
  -H "X-Admin-API-Key: token"
```

#### PUT `/admin/{path}`

**Forward admin requests**

Forwards requests prefixed with /admin to the Admin API service. Requires X-Admin-API-Key.

**Parameters:**

- `path` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X PUT "http://localhost:18056/admin/users/1" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: token" \
  -d '{
    "name": "Updated Name",
    "max_concurrent_bots": 10
  }'
```

#### POST `/admin/{path}`

**Forward admin requests**

Forwards requests prefixed with /admin to the Admin API service. Requires X-Admin-API-Key.

**Parameters:**

- `path` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X POST "http://localhost:18056/admin/users" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: token" \
  -d '{
    "email": "newuser@example.com",
    "name": "New User",
    "max_concurrent_bots": 5
  }'
```

#### DELETE `/admin/{path}`

**Forward admin requests**

Forwards requests prefixed with /admin to the Admin API service. Requires X-Admin-API-Key.

**Parameters:**

- `path` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X DELETE "http://localhost:18056/admin/tokens/1" \
  -H "X-Admin-API-Key: token"
```

#### PATCH `/admin/{path}`

**Forward admin requests**

Forwards requests prefixed with /admin to the Admin API service. Requires X-Admin-API-Key.

**Parameters:**

- `path` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`
- **422** Validation Error
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "detail": [
        {
          "loc": ["string", 0],
          "msg": "string",
          "type": "string"
        }
      ]
    }
    ```

**Curl Command:**

```bash
curl -X PATCH "http://localhost:18056/admin/users/1" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: token" \
  -d '{
    "max_concurrent_bots": 15
  }'
```

---

## Schemas

### BotStatus

```json
{
  "container_id": "string",
  "container_name": "string",
  "platform": "string",
  "native_meeting_id": "string",
  "status": "string",
  "created_at": "string",
  "labels": {
    "additionalProp1": "string",
    "additionalProp2": "string",
    "additionalProp3": "string"
  },
  "meeting_id_from_name": "string"
}
```

**Properties:**

- `container_id` _(string)_ - **Title:** Container Id
- `container_name` _(string)_ - **Title:** Container Name
- `platform` _(string)_ - **Title:** Platform
- `native_meeting_id` _(string)_ - **Title:** Native Meeting Id
- `status` _(string)_ - **Title:** Status
- `created_at` _(string)_ - **Title:** Created At
- `labels` _(object)_ - **Labels:** `< * >: string`
- `meeting_id_from_name` _(string)_ - **Title:** Meeting Id From Name

### BotStatusResponse

```json
{
  "running_bots": [
    {
      "container_id": "Container Id[...]",
      "container_name": "Container Name[...]",
      "platform": "Platform[...]",
      "native_meeting_id": "Native Meeting Id[...]",
      "status": "Status[...]",
      "created_at": "Created At[...]",
      "labels": "Labels{...}",
      "meeting_id_from_name": "Meeting Id From Name[...]"
    }
  ]
}
```

**Properties:**

- `running_bots` _(array, required)_ - **Title:** Running Bots - Array of `BotStatus` objects

### HTTPValidationError

```json
{
  "detail": [
    {
      "loc": ["string", 0],
      "msg": "string",
      "type": "string"
    }
  ]
}
```

**Properties:**

- `detail` _(array)_ - **Title:** Detail - Array of `ValidationError` objects

### MeetingListResponse

```json
{
  "meetings": [
    {
      "id": 0,
      "user_id": 0,
      "platform": "string",
      "native_meeting_id": "string",
      "constructed_meeting_url": "string",
      "status": "string",
      "bot_container_id": "string",
      "start_time": "2025-07-07T10:08:16.677Z",
      "end_time": "2025-07-07T10:08:16.677Z",
      "data": {},
      "created_at": "2025-07-07T10:08:16.677Z",
      "updated_at": "2025-07-07T10:08:16.677Z"
    }
  ]
}
```

**Properties:**

- `meetings` _(array, required)_ - **Title:** Meetings - Array of `MeetingResponse` objects

### MeetingResponse

```json
{
  "id": 0,
  "user_id": 0,
  "platform": "string",
  "native_meeting_id": "string",
  "constructed_meeting_url": "string",
  "status": "string",
  "bot_container_id": "string",
  "start_time": "2025-07-07T10:08:16.677Z",
  "end_time": "2025-07-07T10:08:16.677Z",
  "data": {},
  "created_at": "2025-07-07T10:08:16.677Z",
  "updated_at": "2025-07-07T10:08:16.677Z"
}
```

**Properties:**

- `id` _(integer, required)_ - **Title:** Id - Internal database ID for the meeting
- `user_id` _(integer, required)_ - **Title:** User Id
- `platform` _(Platform string, required)_ - **Title:** Platform - Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot. **Enum:** `[ google_meet, zoom, teams ]`
- `native_meeting_id` _(string)_ - **Title:** Native Meeting Id - The native meeting identifier provided during creation
- `constructed_meeting_url` _(string)_ - **Title:** Constructed Meeting Url - The meeting URL constructed internally, if possible
- `status` _(string, required)_ - **Title:** Status
- `bot_container_id` _(string)_ - **Title:** Bot Container Id
- `start_time` _(string, $date-time)_ - **Title:** Start Time
- `end_time` _(string, $date-time)_ - **Title:** End Time
- `data` _(object)_ - **Data** - JSON data containing meeting metadata like name, participants, languages, and notes
- `created_at` _(string, $date-time, required)_ - **Title:** Created At
- `updated_at` _(string, $date-time, required)_ - **Title:** Updated At

### Platform

**Type:** `string`  
**Title:** Platform  
**Description:** Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot.  
**Enum:** `[ google_meet, zoom, teams ]`

### TranscriptionResponse

```json
{
  "id": 0,
  "platform": "string",
  "native_meeting_id": "string",
  "constructed_meeting_url": "string",
  "status": "string",
  "start_time": "2025-07-07T10:08:16.684Z",
  "end_time": "2025-07-07T10:08:16.684Z",
  "segments": [
    {
      "start": 0,
      "end": 0,
      "text": "string",
      "language": "string",
      "created_at": "2025-07-07T10:08:16.684Z",
      "speaker": "string",
      "absolute_start_time": "2025-07-07T10:08:16.684Z",
      "absolute_end_time": "2025-07-07T10:08:16.684Z"
    }
  ]
}
```

**Description:** Response for getting a meeting's transcript.

**Properties:**

- `id` _(integer, required)_ - **Title:** Id - Internal database ID for the meeting
- `platform` _(Platform string, required)_ - **Title:** Platform - Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot. **Enum:** Array [ 3 ]
- `native_meeting_id` _(string)_ - **Title:** Native Meeting Id
- `constructed_meeting_url` _(string)_ - **Title:** Constructed Meeting Url
- `status` _(string, required)_ - **Title:** Status
- `start_time` _(string, $date-time)_ - **Title:** Start Time
- `end_time` _(string, $date-time)_ - **Title:** End Time
- `segments` _(array, required)_ - **Title:** Segments - List of transcript segments - Array of `TranscriptionSegment` objects

### TranscriptionSegment

```json
{
  "start": 0,
  "end": 0,
  "text": "string",
  "language": "string",
  "created_at": "2025-07-07T10:08:16.684Z",
  "speaker": "string",
  "absolute_start_time": "2025-07-07T10:08:16.684Z",
  "absolute_end_time": "2025-07-07T10:08:16.684Z"
}
```

**Properties:**

- `start` _(number, required)_ - **Title:** Start
- `end` _(number, required)_ - **Title:** End
- `text` _(string, required)_ - **Title:** Text
- `language` _(string)_ - **Title:** Language
- `created_at` _(string, $date-time)_ - **Title:** Created At
- `speaker` _(string)_ - **Title:** Speaker
- `absolute_start_time` _(string, $date-time)_ - **Title:** Absolute Start Time - Absolute start timestamp of the segment (UTC)
- `absolute_end_time` _(string, $date-time)_ - **Title:** Absolute End Time - Absolute end timestamp of the segment (UTC)

### ValidationError

```json
{
  "loc": ["string", 0],
  "msg": "string",
  "type": "string"
}
```

**Properties:**

- `loc` _(array, required)_ - **Title:** Location
  - **anyOf:**
    - `string`
    - `integer`
- `msg` _(string, required)_ - **Title:** Message
- `type` _(string, required)_ - **Title:** Error Type
