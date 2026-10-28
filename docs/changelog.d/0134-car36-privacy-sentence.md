- **The privacy sentence in meeting mail claims only what Vexa enforces (S03-2).** The sentence
  attendees read no longer changes with `agent_reads_desk`, `transcript_retention_days` or
  `recording_retention_days`. Nothing enforces those rules yet, so the mail no longer says that a
  workspace is read only by its owner's agent, that transcripts or recordings are deleted after a
  number of days, or that no recording is kept. `data_statement` still replaces the locality
  clause. `POLICIES.md` now marks the three rules as declared and not yet enforced. See
  [Shared chats](/minutes/shared-chats), which now says the private-source marks live in Redis.
- **A personal workspace says who can read it (S03-2).** Its first line in the workspace panel used
  to say "no agent reads it" when `agent_reads_desk` was off, or "agents read it for meetings you are
  in" when it was on. It now says "everyone at <company> and their agents read it", which is what
  agent-api enforces whatever the rule answers. The post-meeting agent is no longer told that it
  can read the attendees' personal workspaces: none are mounted, and it now reads only its own
  person's.
