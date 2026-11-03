- **A personal transcription endpoint must be public (#1784).** `PUT /user/transcription` refuses an
  internal destination with `422`. A bot sending audio to a person's own endpoint checks every
  request against the same rules as the server-side URL guard, and connects only to the address it
  checked; the transcription Test button does the same. A deployment's own transcription service is
  unaffected. See [Settings](/api/settings).
