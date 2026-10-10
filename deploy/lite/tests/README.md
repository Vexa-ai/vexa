# deploy/lite/tests — Lite deployment checks

- `test_storage_where.py` — offline pytest checks for `make storage-where`, using temporary
  env files and subprocesses; no Docker required.

- `test_storage_credentials.py` — offline: `storage-credentials.sh` mints the storage root pair into
  `.env` and replaces a published one, the entrypoint refuses storage on an unset or published pair,
  and one refused list holds across the script, the Makefile, the entrypoint and compose.

- `test_child_isolation.py` — offline: what the image gives non-root children (read-only browser
  install, root-only Valkey data and store, the tools user, each bot's own screenshot directory,
  PulseAudio socket and X display — its cookie, and the check that its display's sockets are its own
  Xvfb's — no VNC, Valkey's password off its command line, root's runtime directory and the self-host
  keys root-only).

- `test_admin_token.py` — offline: Lite's admin key is never a published value. `make up` replaces an
  unset or published `ADMIN_TOKEN` in `.env` with a minted key, the entrypoint refuses a published
  one, and the refused list is the one admin-api, meeting-api and flows use.

- `test_database_password.py`, `test_dispatch_signing_key.py`, `test_terminal_secret.py` — offline: no
  published database password, dispatch signing key or terminal secret; each is minted (and the last
  two kept across restarts) or refused.

- `test_env_file_hygiene.py`, `test_image_by_path_reads.py`, `test_runtime_environment.py`,
  `test_workspace_store.py` — offline: `.env.example` is a valid `--env-file`; the image ships every
  file its services read by path; what each program's environment holds; agent-api's workspace dir
  and the runtime's mount target are one path.

- `service_checks.py` — LIVE, piped into the booted container by `make -C deploy/lite test`: the
  services' admin key is none of the published values, and admin-api reaches its delegation
  revocation store (its own `is_revoked`, run with its own environment, answers instead of raising).

- `test_image_supply.py` — offline: the runtime image and Lite's runtime venv install nothing
  outside `uv.lock` (the ASGI server is a locked `production` group), uv is a release past the fixed
  advisories, and Lite takes it as a checksum-verified binary rather than a piped script.

- `program_environments.py`, `child_identities.py` — LIVE, run by `make -C deploy/lite test` inside the
  booted container: the runtime caller credential has its three holders only; a worker for a numeric
  and a named subject and a bot started through the runtime run as non-root uids of their own with
  no_new_privs, a dispatch with an unmappable subject starts nothing, and as each child identity no
  other process's environment, root's state or a secret on a command line is readable.

- `concurrent-bots.sh` — the release smoke test and the **sole issuer** of the
  `release/vm-validated` commit status: ≥2 concurrent bots must reach `joining`
  on per-bot profile dirs with zero Chromium SingletonLock signatures (the #478
  failure class fires at browser launch, so no meeting admission is needed), and,
  through `bot_displays.py` run inside while they are up, each on its own X display
  that no other bot can open or capture, and through `bot_browsers.py`, each browser
  sandboxed and holding none of the bot's environment, with user namespaces refused to
  every process but the runtime and the bots.
  Runs in CI as a `release-images / validate-lite` step against the published image, and
  on any clean host after `IMAGE_TAG=vX.Y.Z make lite`; post the attestation with
  `POST_STATUS=1 GIT_SHA=<released sha>` (sole issuer of `release/vm-validated`).
