- **admin-api reaches people's photos with its own storage key.** It used to hold the object store's
  root pair (Compose) or the recordings key (Helm). Now it signs with `VEXA_PHOTOS_S3_ACCESS_KEY` /
  `VEXA_PHOTOS_S3_SECRET_KEY`, limited to `person-photos/` in the recordings bucket. Compose: `make up`
  mints the pair and storage-init makes the account and proves recordings, chat files and bot
  sessions are refused. With your own S3 on Compose, make a key limited to `<bucket>/person-photos/*`
  and set both values in `.env`. Helm: create a Secret with such a key and name it in
  `adminApi.photos.existingSecret` (keys `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` by default);
  until you do, admin-api shows initials. Photos already stored stay where they are and show again
  once the key is set. Naming `storage.s3.existingSecret` there is refused.
