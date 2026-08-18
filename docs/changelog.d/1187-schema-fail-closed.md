- **Self-hosted deployments fail closed when the one-bot-per-meeting guarantee cannot be enforced
  (#1187).** The admin service now refuses to start if its database is missing the unique index
  that prevents duplicate live meetings, instead of starting and allowing duplicates to slip
  through. Operators upgrading an existing deployment apply the schema migration shipped with this
  release before rolling out. See [Deployment](/deployment).
