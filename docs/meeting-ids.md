# Meeting Links & IDs

Vexa uses **user-provided** meeting identifiers. You always pass:

- `platform` (`google_meet` | `teams` | `zoom`)
- `native_meeting_id` (format depends on the platform)

Microsoft Teams meetings also require:

- `passcode` (the `?p=` value from the Teams link)

<Tabs>
<Tab title="Google Meet">
Use the meeting code from the URL:

- URL: `https://meet.google.com/abc-defg-hij`
- `native_meeting_id`: `abc-defg-hij`

Example request body:

```json
{
  "platform": "google_meet",
  "native_meeting_id": "abc-defg-hij"
}
```
</Tab>

<Tab title="Microsoft Teams">
Teams requires **both** the numeric meeting ID and the `p=` value.

- URL: `https://teams.live.com/meet/1234567890123?p=YOUR_PASSCODE`
- `native_meeting_id`: `1234567890123`
- `passcode`: `YOUR_PASSCODE`

Example request body:

```json
{
  "platform": "teams",
  "native_meeting_id": "1234567890123",
  "passcode": "YOUR_PASSCODE"
}
```
</Tab>

<Tab title="Zoom">
Use the numeric meeting ID. If your link has `?pwd=...`, you can pass it as `passcode`.

- URL: `https://us05web.zoom.us/j/12345678901?pwd=...`
- `native_meeting_id`: `12345678901`
- `passcode`: optional (`pwd=...`)

Example request body:

```json
{
  "platform": "zoom",
  "native_meeting_id": "12345678901",
  "passcode": "OPTIONAL_PWD"
}
```

Zoom has additional constraints (OAuth + Meeting SDK + OBF flow) and typically Marketplace approval:

- [Zoom limitations](platforms/zoom.md)
- [Zoom app setup](zoom-app-setup.md)
</Tab>
</Tabs>
