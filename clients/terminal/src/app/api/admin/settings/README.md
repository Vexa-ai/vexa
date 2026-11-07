# settings

GET/PUT /api/admin/settings/{key} — the platform-wide model/transcription defaults (Settings → Models global cards) and who may sign in (Settings → Sign-in, key `signin`), proxied to admin-api's internal `/internal/settings/{key}`. Unlike the sibling routes this one WRITES.
