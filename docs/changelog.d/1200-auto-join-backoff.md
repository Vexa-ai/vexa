- **A failed auto-join retries on a bounded backoff instead of repeatedly (#1200).** When a
  calendar occurrence's bot fails to join, the next attempt waits out a fixed backoff, and the
  reason for the wait is readable on the meeting instead of being silent.
