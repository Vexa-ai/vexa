- **Webhook signing secrets are sealed at rest (#1868).** The secret you set for your webhook is
  stored AES-256-GCM sealed under a key kept outside the database (`VEXA_AT_REST_KEY_FILE`), on
  your account, on each meeting and in the retry queue. Existing secrets are sealed in place on
  upgrade; signatures don't change, so your receiver keeps verifying.
