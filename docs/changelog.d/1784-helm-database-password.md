- **Helm: the bundled database has no published password (#1784).** `database.password` no longer
  defaults to `postgres`. Left empty, the chart generates one at install and keeps it across
  upgrades; an explicit value must be 32+ bytes and not a published one. An install whose database
  still has `postgres` is moved to a new password by a hook after its next `helm upgrade`, which also
  restarts the services that read it. **If you set `database.password: postgres` in your own values,
  remove it before upgrading.** A GitOps tool that renders with `helm template` cannot read the
  existing Secret: set `database.password` explicitly. An external database is untouched.
