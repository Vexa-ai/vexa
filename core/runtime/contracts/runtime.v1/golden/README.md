# golden — runtime.v1 vectors

Conforming example payloads (the spec, P8). Filenames declare the shape:
`spec-*` → `WorkloadSpec`, `status-*` → `WorkloadStatus`, `event-*` → `RuntimeEvent`,
`error-*` → `Error` (the 401 without the caller credential, the 400 for a mount outside the store).
`signed-*` → `SignedEventVector`: a RuntimeEvent, a published test token, the delivery URL, the
signing time and the exact `X-Runtime-Signature`; `validate.mjs` re-signs it, and the runtime's and meeting-api's tests pin the
same vector.
Validated against `../runtime.schema.json` by `../validate.mjs` (run by `gate:schema`).
