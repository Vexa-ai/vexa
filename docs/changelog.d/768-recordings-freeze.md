- **Recordings: opening a recording mid-meeting no longer freezes it (#768).** Master assembly is now
  re-assemblable instead of write-once — a `GET /recordings/{id}/master` while the meeting is still
  recording serves the audio captured so far without permanently freezing the file, and every later
  read that finds new chunks rebuilds the master (repairing already-frozen recordings on their next
  read). The assembled-chunk-count is recorded and compared so the freeze cannot silently return.
