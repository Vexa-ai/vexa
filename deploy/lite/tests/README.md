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
  that no other bot can open or capture.
  Runs in CI as a `release-images / validate-lite` step against the published image, and
  on any clean host after `IMAGE_TAG=vX.Y.Z make lite`; post the attestation with
  `POST_STATUS=1 GIT_SHA=<released sha>` (sole issuer of `release/vm-validated`).
