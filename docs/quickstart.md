# Quickstart

Get from **meeting link → transcript (and optional recording playback)** in a few minutes.

If you’re not sure what to pass as `native_meeting_id` (or when `passcode` is required), read:

- [Meeting links & IDs](meeting-ids.md)

<Steps>
  <Step title="Set your API base + key">
    ```bash
    export API_BASE="http://localhost:8056"   # or https://api.cloud.vexa.ai
    export API_KEY="YOUR_API_KEY_HERE"
    ```
  </Step>

  <Step title="Send a bot to a meeting">
    <Tabs>
      <Tab title="Google Meet">
        ```bash
        curl -X POST "$API_BASE/bots" \
          -H "Content-Type: application/json" \
          -H "X-API-Key: $API_KEY" \
          -d '{
            "platform": "google_meet",
            "native_meeting_id": "abc-defg-hij",
            "recording_enabled": true,
            "transcribe_enabled": true,
            "transcription_tier": "realtime"
          }'
        ```
      </Tab>
      <Tab title="Microsoft Teams">
        ```bash
        curl -X POST "$API_BASE/bots" \
          -H "Content-Type: application/json" \
          -H "X-API-Key: $API_KEY" \
          -d '{
            "platform": "teams",
            "native_meeting_id": "1234567890123",
            "passcode": "YOUR_TEAMS_P_VALUE",
            "recording_enabled": true,
            "transcribe_enabled": true,
            "transcription_tier": "realtime"
          }'
        ```
      </Tab>
      <Tab title="Zoom">
        > Zoom requires extra setup and typically Marketplace approval.
        >
        > See: [`docs/platforms/zoom.md`](platforms/zoom.md) and [`docs/zoom-app-setup.md`](zoom-app-setup.md)

        ```bash
        curl -X POST "$API_BASE/bots" \
          -H "Content-Type: application/json" \
          -H "X-API-Key: $API_KEY" \
          -d '{
            "platform": "zoom",
            "native_meeting_id": "12345678901",
            "passcode": "OPTIONAL_PWD",
            "recording_enabled": true,
            "transcribe_enabled": true,
            "transcription_tier": "realtime"
          }'
        ```
      </Tab>
    </Tabs>

    Full reference: [Bots API](api/bots.md)
  </Step>

  <Step title="(Recommended) Configure a webhook for completion">
    Webhooks are the easiest way to know when post-meeting artifacts are ready.

    ```bash
    curl -X PUT "$API_BASE/user/webhook" \
      -H "Content-Type: application/json" \
      -H "X-API-Key: $API_KEY" \
      -d '{
        "webhook_url": "https://your-service.com/vexa/webhook",
        "webhook_secret": "optional-shared-secret"
      }'
    ```

    - Webhook guide: [`docs/webhooks.md`](webhooks.md)
    - Local dev tunneling: [`docs/local-webhook-development.md`](local-webhook-development.md)
  </Step>

  <Step title="Fetch the transcript (and recording metadata, if present)">
    ```bash
    curl -H "X-API-Key: $API_KEY" \
      "$API_BASE/transcripts/google_meet/abc-defg-hij"
    ```

    The response contains:

    - `segments[]`: transcript segments with `start_time`/`end_time`
    - `recordings[]` (optional): recording + `media_files[]` for playback/download

    Full reference: [Transcripts API](api/transcripts.md)
  </Step>
</Steps>

## Next Steps

- Live streaming: [WebSocket guide](websocket.md)
- Post-meeting playback: [Recordings API](api/recordings.md) + [Recording storage](recording-storage.md)
- Delete/anonymize: [Meetings API](api/meetings.md) (and read: [Errors and retries](errors-and-retries.md))
