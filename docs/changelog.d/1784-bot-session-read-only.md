- **Authenticated bots read the stored browser session; only meeting-api writes it (#1784).** The
  bots' `BOT_S3_*` pair is now read-only: Compose's storage-init grants it list and read on
  `BOT_USERDATA_S3_PATH` and nothing else, and a bot restores only the session profile (cookies,
  login data, preferences, web data, local and session storage). On teardown a bot hands its rotated
  session to meeting-api with its session token; meeting-api accepts it only from the live
  authenticated bot, only those files, and writes it with its own storage credentials. `POST /bots`
  refuses to spawn when either half of `BOT_S3_*` equals `MINIO_*` or `S3_*`. **`make login` now
  uploads with `LOGIN_S3_ACCESS_KEY` / `LOGIN_S3_SECRET_KEY`** (on Compose, `.env`'s
  `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`). On Helm, make the bots' key read-only on the prefix and
  let meeting-api's S3 credentials write it. Upgrade bots and meeting-api together. See
  [Authenticated bots](/authenticated-bots#storage-layout-and-security).
