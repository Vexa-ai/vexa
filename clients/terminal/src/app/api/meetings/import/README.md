# api/meetings/import

`POST /api/meetings/import[?dry_run=true]` — streams a meeting-bundle.v1 zip to the gateway's `POST /meetings/import` and returns its JSON (the preview, the new meeting, or a refusal with its code). See `../README.md`.
