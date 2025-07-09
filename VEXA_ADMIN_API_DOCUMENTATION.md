# Vexa Admin API Documentation

**Version:** 0.1.0  
**OAS 3.1** | [/openapi.json](http://localhost:18057/openapi.json)

---

## API Endpoints

### Admin

#### GET `/admin/users`

**List all users**

**Parameters:**

- `skip` _(integer, query)_
  - **Default value:** 0
- `limit` _(integer, query)_
  - **Default value:** 100

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    [
      {
        "email": "user@example.com",
        "name": "string",
        "image_url": "string",
        "max_concurrent_bots": 0,
        "data": {},
        "id": 0,
        "created_at": "2025-07-07T10:27:30.123Z"
      }
    ]
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
curl -X GET "http://localhost:18057/admin/users" \
  -H "X-Admin-API-Key: token"
```

#### POST `/admin/users`

**Find or create a user by email**

**Parameters:** No parameters

**Request body:** `application/json`

**Example Value:**

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0,
  "data": {}
}
```

**Responses:**

- **200** User found and returned
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.125Z"
    }
    ```
- **201** User created successfully
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.126Z"
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
curl -X POST "http://localhost:18057/admin/users" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: token" \
  -d '{
    "email": "user@example.com",
    "name": "John Doe",
    "max_concurrent_bots": 5
  }'
```

#### GET `/admin/users/email/{user_email}`

**Get a specific user by email**

Gets a user by their email.

**Parameters:**

- `user_email` _(string, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.127Z"
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
curl -X GET "http://localhost:18057/admin/users/email/user@example.com" \
  -H "X-Admin-API-Key: token"
```

#### GET `/admin/users/{user_id}`

**Get a specific user by ID, including their API tokens**

Gets a user by their ID, eagerly loading their API tokens.

**Parameters:**

- `user_id` _(integer, path, required)_

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.128Z",
      "api_tokens": []
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
curl -X GET "http://localhost:18057/admin/users/4" \
  -H "X-Admin-API-Key: token"
```

#### PATCH `/admin/users/{user_id}`

**Update user details**

Update user's name, image URL, or max concurrent bots.

**Parameters:**

- `user_id` _(integer, path, required)_

**Request body:** `application/json`

**Example Value:**

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0
}
```

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.130Z"
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
curl -X PATCH "http://localhost:18057/admin/users/4" \
  -H "Content-Type: application/json" \
  -H "X-Admin-API-Key: token" \
  -d '{
    "name": "Updated Name",
    "max_concurrent_bots": 10
  }'
```

#### POST `/admin/users/{user_id}/tokens`

**Generate a new API token for a user**

**Parameters:**

- `user_id` _(integer, path, required)_

**Responses:**

- **201** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "user_id": 0,
      "id": 0,
      "token": "string",
      "created_at": "2025-07-07T10:27:30.131Z"
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
curl -X POST "http://localhost:18057/admin/users/4/tokens" \
  -H "X-Admin-API-Key: token"
```

#### DELETE `/admin/tokens/{token_id}`

**Revoke/Delete an API token by its ID**

Deletes an API token by its database ID.

**Parameters:**

- `token_id` _(integer, path, required)_

**Responses:**

- **204** Successful Response
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
curl -X DELETE "http://localhost:18057/admin/tokens/1" \
  -H "X-Admin-API-Key: token"
```

#### GET `/admin/stats/meetings-users`

**Get paginated list of meetings joined with users**

Retrieves a paginated list of all meetings, with user details embedded. This provides a comprehensive overview for administrators.

**Parameters:**

- `skip` _(integer, query)_
  - **Default value:** 0
- `limit` _(integer, query)_
  - **Default value:** 100

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "total": 0,
      "items": [
        {
          "id": 0,
          "user_id": 0,
          "platform": "google_meet",
          "native_meeting_id": "string",
          "constructed_meeting_url": "string",
          "status": "string",
          "bot_container_id": "string",
          "start_time": "2025-07-07T10:27:30.133Z",
          "end_time": "2025-07-07T10:27:30.134Z",
          "data": {},
          "created_at": "2025-07-07T10:27:30.134Z",
          "updated_at": "2025-07-07T10:27:30.134Z",
          "user": {
            "email": "user@example.com",
            "name": "string",
            "image_url": "string",
            "max_concurrent_bots": 0,
            "data": {},
            "id": 0,
            "created_at": "2025-07-07T10:27:30.134Z"
          }
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
curl -X GET "http://localhost:18057/admin/stats/meetings-users?skip=0&limit=10" \
  -H "X-Admin-API-Key: token"
```

---

### User

#### PUT `/user/webhook`

**Set user webhook URL**

Set a webhook URL for the authenticated user to receive notifications.

**Parameters:** No parameters

**Request body:** `application/json`

**Example Value:**

```json
{
  "webhook_url": "https://example.com/"
}
```

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:**
    ```json
    {
      "email": "user@example.com",
      "name": "string",
      "image_url": "string",
      "max_concurrent_bots": 0,
      "data": {},
      "id": 0,
      "created_at": "2025-07-07T10:27:30.135Z"
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
curl -X PUT "http://localhost:18057/user/webhook" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV" \
  -d '{
    "webhook_url": "https://your-endpoint.com/api/webhook"
  }'
```

---

### Default

#### GET `/`

**Root**

**Parameters:** No parameters

**Responses:**

- **200** Successful Response
  - **Media type:** `application/json`
  - **Example Value:** `"string"`
  - **Schema:** `"string"`

**Curl Command:**

```bash
curl -X GET "http://localhost:18057/"
```

---

## Schemas

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

- `detail` _(array)_ - Array of `ValidationError` objects
  - **Items:** `ValidationError` objects
    - `loc` _(array)_ - Array of `(string | integer)`
      - **Items:** `(string | integer)`
        - **Any of:**
          - `#0` string
          - `#1` integer
    - `msg` _(string)_
    - `type` _(string)_

### MeetingUserStat

```json
{
  "id": 0,
  "user_id": 0,
  "platform": "google_meet",
  "native_meeting_id": "string",
  "constructed_meeting_url": "string",
  "status": "string",
  "bot_container_id": "string",
  "start_time": "2025-07-07T10:27:30.133Z",
  "end_time": "2025-07-07T10:27:30.134Z",
  "data": {},
  "created_at": "2025-07-07T10:27:30.134Z",
  "updated_at": "2025-07-07T10:27:30.134Z",
  "user": {
    "email": "user@example.com",
    "name": "string",
    "image_url": "string",
    "max_concurrent_bots": 0,
    "data": {},
    "id": 0,
    "created_at": "2025-07-07T10:27:30.134Z"
  }
}
```

**Properties:**

- `id` _(integer)_ - Internal database ID for the meeting
- `user_id` _(integer)_
- `platform` _(string)_ - Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot.
  - **Enum:**
    - `#0` "google_meet"
    - `#1` "zoom"
    - `#2` "teams"
- `native_meeting_id` _(string)_ - The native meeting identifier provided during creation
- `constructed_meeting_url` _(string)_ - The meeting URL constructed internally, if possible
- `status` _(string)_
- `bot_container_id` _(string)_
- `start_time` _(string, date-time)_
- `end_time` _(string, date-time)_
- `data` _(object)_ - JSON data containing meeting metadata like name, participants, languages, and notes
- `created_at` _(string, date-time)_
- `updated_at` _(string, date-time)_
- `user` _(object)_
  - `email` _(string, email)_
  - `name` _(string)_
  - `image_url` _(string)_
  - `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user
  - `data` _(object)_ - JSONB storage for arbitrary user data, like webhook URLs
  - `id` _(integer)_
  - `created_at` _(string, date-time)_

### PaginatedMeetingUserStatResponse

```json
{
  "total": 0,
  "items": [
    {
      "id": 0,
      "user_id": 0,
      "platform": "google_meet",
      "native_meeting_id": "string",
      "constructed_meeting_url": "string",
      "status": "string",
      "bot_container_id": "string",
      "start_time": "2025-07-07T10:27:30.133Z",
      "end_time": "2025-07-07T10:27:30.134Z",
      "data": {},
      "created_at": "2025-07-07T10:27:30.134Z",
      "updated_at": "2025-07-07T10:27:30.134Z",
      "user": {
        "email": "user@example.com",
        "name": "string",
        "image_url": "string",
        "max_concurrent_bots": 0,
        "data": {},
        "id": 0,
        "created_at": "2025-07-07T10:27:30.134Z"
      }
    }
  ]
}
```

**Properties:**

- `total` _(integer)_
- `items` _(array)_ - Array of `MeetingUserStat` objects
  - **Items:** `MeetingUserStat` objects
    - `id` _(integer)_ - Internal database ID for the meeting
    - `user_id` _(integer)_
    - `platform` _(string)_ - Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot.
      - **Enum:**
        - `#0` "google_meet"
        - `#1` "zoom"
        - `#2` "teams"
    - `native_meeting_id` _(string)_ - The native meeting identifier provided during creation
    - `constructed_meeting_url` _(string)_ - The meeting URL constructed internally, if possible
    - `status` _(string)_
    - `bot_container_id` _(string)_
    - `start_time` _(string, date-time)_
    - `end_time` _(string, date-time)_
    - `data` _(object)_ - JSON data containing meeting metadata like name, participants, languages, and notes
    - `created_at` _(string, date-time)_
    - `updated_at` _(string, date-time)_
    - `user` _(object)_
      - `email` _(string, email)_
      - `name` _(string)_
      - `image_url` _(string)_
      - `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user
      - `data` _(object)_ - JSONB storage for arbitrary user data, like webhook URLs
      - `id` _(integer)_
      - `created_at` _(string, date-time)_

### Platform

**Type:** `string`  
**Description:** Platform identifiers for meeting platforms. The value is the external API name, while the bot_name is what's used internally by the bot.  
**Enum:**

- `#0` "google_meet"
- `#1` "zoom"
- `#2` "teams"

### TokenResponse

```json
{
  "user_id": 0,
  "id": 0,
  "token": "string",
  "created_at": "2025-07-07T10:27:30.131Z"
}
```

**Properties:**

- `user_id` _(integer)_
- `id` _(integer)_
- `token` _(string)_
- `created_at` _(string, date-time)_

### UserCreate

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0,
  "data": {}
}
```

**Properties:**

- `email` _(string, email)_
- `name` _(string)_
- `image_url` _(string)_
- `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user
- `data` _(object)_ - JSONB storage for arbitrary user data, like webhook URLs

### UserDetailResponse

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0,
  "data": {},
  "id": 0,
  "created_at": "2025-07-07T10:27:30.128Z",
  "api_tokens": []
}
```

**Properties:**

- `email` _(string, email)_
- `name` _(string)_
- `image_url` _(string)_
- `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user
- `data` _(object)_ - JSONB storage for arbitrary user data, like webhook URLs
- `id` _(integer)_
- `created_at` _(string, date-time)_
- `api_tokens` _(array)_ - Array of token objects
  - **Items:** Token objects
    - `user_id` _(integer)_
    - `id` _(integer)_
    - `token` _(string)_
    - `created_at` _(string, date-time)_
  - **Default:** empty array

### UserResponse

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0,
  "data": {},
  "id": 0,
  "created_at": "2025-07-07T10:27:30.125Z"
}
```

**Properties:**

- `email` _(string, email)_
- `name` _(string)_
- `image_url` _(string)_
- `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user
- `data` _(object)_ - JSONB storage for arbitrary user data, like webhook URLs
- `id` _(integer)_
- `created_at` _(string, date-time)_

### UserUpdate

```json
{
  "email": "user@example.com",
  "name": "string",
  "image_url": "string",
  "max_concurrent_bots": 0
}
```

**Properties:**

- `email` _(string, email)_
- `name` _(string)_
- `image_url` _(string)_
- `max_concurrent_bots` _(integer)_ - Maximum number of concurrent bots allowed for the user

### ValidationError

```json
{
  "loc": ["string", 0],
  "msg": "string",
  "type": "string"
}
```

**Properties:**

- `loc` _(array)_ - Array of `(string | integer)`
  - **Items:** `(string | integer)`
    - **Any of:**
      - `#0` string
      - `#1` integer
- `msg` _(string)_
- `type` _(string)_

### WebhookUpdate

```json
{
  "webhook_url": "https://example.com/"
}
```

**Properties:**

- `webhook_url` _(string, uri)_ - [1, 2083] characters

# Create a user

curl -X POST "http://localhost:18057/admin/users" \
 -H "Content-Type: application/json" \
 -H "X-Admin-API-Key: token" \
 -d '{
"email": "youssef@faktions.com",
"name": "Youssef",
"max_concurrent_bots": 100
}'

# Generate API token for the user (replace USER_ID with the returned user ID)

curl -X POST "http://localhost:18057/admin/users/4/tokens" \
 -H "X-Admin-API-Key: token"

-> VEXA_API_KEY=PPc8kVSfyoFJ68qgv2FX5wdVQsKoE0rvrc751aQV

# Update max_concurrent_bots for a specific user

curl -X PATCH "http://localhost:18057/admin/users/4" \
 -H "Content-Type: application/json" \
 -H "X-Admin-API-Key: token" \
 -d '{
"max_concurrent_bots": 5
}'

# Send a bot to a meeting

curl -X POST http://localhost:18056/bots \
 -H "Content-Type: application/json" \
 -H "X-API-Key: Be5bpjeYgpT12dfmsPLWYnxmsVFk84yuDoEl7fCl" \
 -d '{
"platform": "teams",
"native_meeting_id": "9336200045903",
"bot_name": "TranscriptBot",
"task": "transcribe"
}'
