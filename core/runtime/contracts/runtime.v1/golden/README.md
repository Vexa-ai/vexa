# golden — runtime.v1 vectors

Conforming example payloads (the spec, P8). Filenames declare the shape:
`spec-*` → `WorkloadSpec`, `status-*` → `WorkloadStatus`, `event-*` → `RuntimeEvent`,
`error-*` → `Error` (the 401 without the caller credential, the 400 for a mount outside the store).
Validated against `../runtime.schema.json` by `../validate.mjs` (run by `gate:schema`).
