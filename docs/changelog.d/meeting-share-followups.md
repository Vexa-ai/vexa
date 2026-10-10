- **Meeting-share follow-ups.** The invite mail's send-time link is refused (`409`) if the owner withdrew
  the invite, or removed the person, after pressing Invite — nothing is mailed then. Binding a meeting
  to a workspace is refused for an agent worker running with nobody in the loop, on every path. The
  owner's access view asks identity about unnamed readers concurrently, at most 25 at a time, and
  leaves a reader it could not name alone for a day.
