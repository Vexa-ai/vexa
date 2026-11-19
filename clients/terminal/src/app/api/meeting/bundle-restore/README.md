# api/meeting/bundle-restore

`POST /api/meeting/bundle-restore?meeting_id=` — streams the bundle a meeting was just imported from
to the gateway's `/agent/meeting/bundle-restore`, which lands its workspace and notes page for the
caller. JSON answer passed through with its status. Refused (404) in meetings-only mode.
