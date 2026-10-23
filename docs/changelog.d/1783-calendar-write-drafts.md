- **Calendar changes and richer Gmail drafts, under an administrator's policy (#1783).** With the new
  **Enable calendar changes** consent in Connections, an agent can create, update and cancel Google
  Calendar events (`calendar_event_create`, `calendar_event_update`, `calendar_event_cancel`):
  attendees are not emailed unless asked and allowed, only events the person can change are
  touched, and retries are idempotent. Drafts gain replies in a thread (correct `In-Reply-To`,
  `References` and `Re:` subject), Cc and Bcc, workspace attachments, and `gmail_draft_update` for
  drafts Vexa created; nothing is ever sent. What an agent may do — per action while the person is
  present, which reads a background routine may take, attendee mail, attendee and recipient
  domains, calendars, attachments — is set by new keys in `_global/POLICIES.md`, enforced by
  agent-api and again by the credential broker; a change to one of them is refused to a background
  routine and named in `_global`'s history. Each person can narrow them for themselves
  (`connection_policy_set`, from their own client, never by an agent) and never widen them. Drafts
  and calendar changes always need the person in the chat; defaults keep today's behaviour for
  people in the chat and refuse all mail and calendar actions in background routines. Google only; Outlook is not included. See
  [Connections](/connections#what-an-agent-may-do-with-mail-and-calendar).
