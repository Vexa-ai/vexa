- **The transcript search index builds.** meeting-api's single-flight guard holds its lock outside
  any transaction, so `CREATE INDEX CONCURRENTLY ix_transcription_text_fts` no longer waits on its
  own guard for the life of the pod. Each wait of the build is bounded (5 s); a failed build logs
  the statement to run by hand, and the next boot drops the INVALID leftover and builds again. A
  stuck build no longer holds back schema changes on `transcriptions`.
- **A key with empty scopes stays without access.** The scope backfill (MIGRATION-0004) now runs
  only in the convergence that adds `api_tokens.scopes` to a 0.10-era table. A key stored with
  `scopes = '{}'` was refused on 0.12 and stays refused after an admin-api restart or an upgrade.
