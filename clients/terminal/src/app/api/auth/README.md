# auth

Terminal-owned authentication (direct email login, no SMTP). Server routes mint/validate the
per-user `vexa-token` via admin-api; `adminApi.ts` is the server-only admin-api client. See `login/`,
`me/`, `logout/`.
