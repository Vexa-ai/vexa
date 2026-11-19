- **A meeting link's passcode stays out of logs, errors and webhooks; a Zoom host token is never
  stored.** Bot logs, a failed bot's stored reason (`last_error`, `join_evidence`, the status
  history and the `meeting.status_change` webhook), routine chat lines and agent logs now name a
  meeting link with its passcode masked: Zoom `pwd`/`password`, Teams `p`, Jitsi `jwt` and any
  `*key*`/`*secret*`/`*token*` parameter read `***`, while the host and meeting id stay. A Zoom
  `zak` (the host's start token) is dropped when a link arrives through `POST /bots`, a planned
  meeting or calendar sync, so it reaches no meeting row, no calendar snapshot and no bot. Rows
  stored before this release keep their host tokens, and the passcodes their failure diagnostics
  quoted, until an operator runs `python -m meeting_api.scrub_host_tokens` (dry run by default;
  `--apply` rewrites; stored join links keep their passcode so auto-join still works).
