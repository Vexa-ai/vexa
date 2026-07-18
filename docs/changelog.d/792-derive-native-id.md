- **`POST /bots` with only a `meeting_url` now derives the meeting id — no more orphan meetings
  (#792).** A url-only body used to return 201 while persisting `native_meeting_id=''`, creating a
  meeting (and a live bot) no `DELETE /bots/...` or `GET /transcripts/...` could ever address. The
  API now honors what api.v1 always promised: the URL is parsed to extract `platform`,
  `native_meeting_id`, and the passcode (Zoom `?pwd=` / Teams `?p=`); an unrecognizable URL is a
  typed `422` naming the missing `native_meeting_id`, and a `platform` that disagrees with the URL
  is a `422` too. Explicit `native_meeting_id` is never overridden. See
  [Send a bot](/how-to/send-a-bot).
