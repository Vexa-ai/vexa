- **Connections: the credential broker's state is backed up (#1868).** Its two databases now run in
  WAL mode, and a scheduled job, on by default, writes a consistent snapshot every 6 hours into a
  volume of its own and keeps the newest 28. Snapshots hold no key: keep the store key separately.
  See [Connections § Backup and restore](/connections#backup-and-restore).
