- **Lite: the database password has no default (#1784).** The Lite image no longer falls back to
  `postgres` for `DB_PASSWORD`, and its entrypoint refuses to start on an unset or published value.
  `make lite` / `make -C deploy/lite up` mints a password into the repo-root `.env` and sets it on the
  bundled postgres sidecar on every run, so an existing install moves to it on its next `make up`
  with no separate step. **If you run the Lite image yourself, pass `DB_PASSWORD`** (your database's
  password). Lite's runtime caller credential now reaches only the runtime, agent-api and
  meeting-api, and supervisord runs a root-only rendered copy of its config
  (`/run/vexa/supervisord.conf`); `supervisorctl` works as before.
