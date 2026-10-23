- **People have their own photo, shown only to people who work with them (#1783).** Your upload
  comes first (Settings → Your photo, or "Change photo" on your own avatar). After that comes the
  photo from your Google, Microsoft or OIDC sign-in, collected when you sign in and checked again
  at later sign-ins, then your Gravatar if you have one, then your initials. Only you can change
  your photo; an agent can suggest one, but it cannot set one. A photo is visible only to people
  who share a workspace or a chat with you. Every stored photo is re-encoded, so no location or
  other metadata survives. Vexa no longer fetches a Gravatar for anyone else, so contacts who are
  not users are shown with their initials and their company's logo, never a face. See
  [Your photo](/how-to/your-photo).
- **Operators: photos live in your object store (#1783).** admin-api now reaches the same S3 store
  and bucket meeting-api uses for recordings (`S3_*` / `MINIO_*`, set on admin-api by Compose, the
  Helm chart from `storage.s3`, and Lite), under the prefix `person-photos/<user id>/`. Without a
  store, no photos are kept and initials are shown. `VEXA_PERSON_GRAVATAR=false` turns the Gravatar
  check off. admin-api now also reads the terminal's `GOOGLE_CLIENT_ID`, `VEXA_OIDC_ISSUER`,
  `VEXA_OIDC_CLIENT_ID` and `VEXA_OIDC_CA_FILE`, set by Compose and the Helm chart, to verify a
  sign-in's ID token before it takes the provider photo. **Upgrade and rollback:** the change is additive. admin-api creates one new table,
  `person_photos`, at boot, and creating its foreign key briefly takes a SHARE ROW EXCLUSIVE lock
  on `users`. Older versions ignore the table. To roll back, run `DROP TABLE person_photos;` and
  delete the objects under `person-photos/` in the bucket.
