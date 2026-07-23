- **Admin token mint honors JSON `scopes` (and refuses unknown body fields) (#922).**
  `POST /admin/users/{id}/tokens` with `{"scopes":["bot","tx"]}` now mints those scopes instead of
  silently falling through to `["bot"]`. Query `?scopes=` / `?scope=` still work; unsupported body
  fields return `422`. See [Authentication](/authentication).
