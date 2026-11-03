- **Admin account reads carry no stored credential (#1784).** `GET`, `POST` and `PATCH` on
  `/admin/users*` show a person's model key and transcription token as `api_key_set` / `token_set`
  plus a masked tail, and a calendar feed URL as `ics_url_set` plus its masked form. Writing the
  masked value back to a key changes nothing. See [Settings](/api/settings).
