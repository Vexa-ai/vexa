- **Upgrades no longer hold sign-in behind an index build.** admin-api's schema convergence builds a
  missing non-unique index (on 0.12 → 0.13.4: `ix_users_public_profile_handle` on `users`) with
  `CREATE INDEX CONCURRENTLY` after its transaction commits, instead of inside the transaction that
  holds `users` and `api_tokens` for its additive columns. Every `/internal/validate` waited for that
  build before. A failed or interrupted build is logged with the statement to run by hand and is
  rebuilt by the next convergence; unique indexes are unchanged (built in-band, fail closed).
