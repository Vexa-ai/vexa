- **Schema migrations can run before the new pods start (#1783).** `migrations.enabled: true` (Helm)
  runs `python -m admin_api.migrate`, admin-api's own schema convergence, as a pre-upgrade Job.
  It finishes before any new admin-api or meeting-api pod starts, so both services roll out with
  new pods beside the old ones. Compose runs the same step as the one-shot service
  `admin-schema-init`. The Job previously called a module that no longer exists.
