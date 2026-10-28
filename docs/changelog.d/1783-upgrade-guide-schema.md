- **Upgrading from 0.12 names the database changes (#1783).** The upgrade guide lists the session
  store's tables, the `flow_version` `scope` and `cron` columns and its new primary key, and the
  `friction_occurrence` table; when each is applied; and `python -m control_plane.session_migrate`,
  which moves existing chats into the store. It also covers the `vexaai/vexa-*` image names, calls,
  voice notes, and the credential broker and flows services. See
  [Upgrading from 0.12 to 0.13](/upgrade-from-0-12).
