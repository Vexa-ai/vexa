- **Live transcripts (Google Meet, Zoom): a speaker's drafts no longer jump between unrelated phrases
  (#1973).** Every open draft stays visible, in time order, until the reconfirmed segment replaces it.
  The live WebSocket's `transcript` frame now always carries the speaker's complete open-draft set, and
  the collector sends the speaker's updated frame after a `transcript_retract`. This supersedes #1778
  ("The live tail always shows the latest draft") for the dashboard.
