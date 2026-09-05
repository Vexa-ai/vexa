- **Two flows names change, both honoured for one release (#1456).** admin-api reads
  `VEXA_FLOWS_API_URL`; the bare `FLOWS_API_URL` still works and the new name wins when both are
  set. flows-api's operator key travels as `X-Flows-Operator-Key`; `X-Flows-Admin-Key` is still
  accepted and warns once per process. The old name read as admin-api's token, which it never was —
  a start script once carried `changeme` for it on exactly that misreading. Migrate before 0.12.28.
