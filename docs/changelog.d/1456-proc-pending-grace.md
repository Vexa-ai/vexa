- **`PROC_PENDING_GRACE_SEC` is gone (#1456).** The pending-processed drain it timed was removed
  with the collector's writer path; the variable is no longer read. Delete it from your `.env` — it
  is silently ignored.
