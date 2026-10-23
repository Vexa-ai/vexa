- **A worker's push is held on the workspace's real member list (s3 backend, M2-b).** The private-write
  hold on a pushed commit now counts the other readers from the membership store that decides access,
  the same audience a page write is held on, instead of the `policy/members.json` copy in the tree. A
  missing or stale copy no longer lets a push through unheld. When the membership store does not
  answer, a push from a session that read private data is refused with `503 policy_unreadable`. See
  [Workspaces](/core/workspaces).
- **A CRM profile is claimed only through the `email:` match identifier (A2-b).** A record's `Email`
  field no longer makes it claimable, and a record the claimant merged another record into is not
  theirs to claim, so a CRM writer cannot type their own address onto someone's record and take it
  over. A record the org created is claimable by the person it describes when it carries their
  `email:` identifier. Records that only have the address in `Email` are no longer offered: give them
  the identifier, or the person gets a record of their own at sign-in. See [CRM](/minutes/crm).
- **The worker's own commit hold counts the same readers (volume backend, M2-b).** A turn that read
  private data and then wrote files into a workspace others read is held at the commit on the member
  list agent-api read from the membership store for that dispatch and turn, no longer on the tree's
  `policy/members.json`. A mount whose list could not be read is held.
