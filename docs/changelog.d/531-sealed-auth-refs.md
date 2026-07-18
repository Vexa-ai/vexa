- **Sealed `api.v1` contract: security references now resolve (#531, closes #62).** All 61
  secured operations in the sealed OpenAPI document referenced `APIKeyHeader` — a scheme the
  document never defines — so Swagger UI's Authorize dialog could not attach the key and
  spec-driven client generators emitted clients that never sent `X-API-Key`. The per-operation
  references now point at the document's own defined schemes: `ApiKeyAuth` (`X-API-Key`) on the
  56 client operations, `AdminApiKeyAuth` (`X-Admin-API-Key`) on the 5 `/admin/{path}`
  operations — the headers the running gateway and admin-api already honor. `gate:schema`'s
  `validate.mjs` now pins referential integrity (every referenced scheme must be defined, first
  offenders named), so a re-capture can never re-import the bug.
