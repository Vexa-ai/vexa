- **A broken authenticated-bot store stops meeting-api at boot (#1784).** With `BOT_AUTHENTICATED` on,
  an incomplete `BOT_USERDATA_S3_PATH` / `BOT_S3_ENDPOINT` / `BOT_S3_BUCKET`, or a `BOT_S3_*` key that
  equals a storage root key, now refuses the boot and names the variables. It used to surface as a
  503 on the first `POST /bots`.
