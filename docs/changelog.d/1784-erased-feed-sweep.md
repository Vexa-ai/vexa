- **Deleted meetings stay deleted in Redis (#1784).** A transcript segment that arrives after its
  meeting was deleted is now dropped instead of re-creating the meeting's live feed. For meetings
  deleted on an earlier release, run the one-time sweep
  `python -m meeting_api.collector.erased_feed_sweep` in a meeting-api container to remove the feed
  they kept. See [One-time steps after upgrading](/deployment#one-time-steps-after-upgrading).
