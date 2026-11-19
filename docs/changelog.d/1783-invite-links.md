- **Share a workspace by link, and invite people to a meeting into it (#1783).** Ask the agent for a
  link to a workspace (`workspace_invite_link_create`): up to N people (default 10, for 7 days) can
  open `/join/<token>`, sign in, and join it; links can be listed and revoked. The person who made the
  link is notified each time someone joins, and people who joined appear in the @ picker and people
  search. `calendar_notify_attendees` is now **on** by default, so when you ask the agent to create a
  meeting with attendees Google sends them the invitation; each notified change is audited, and a
  company that wants it off sets `calendar_notify_attendees: off` in `_global/POLICIES.md`. An
  existing `_global/POLICIES.md` keeps the value it has. See [Workspaces](/minutes/workspaces#share-a-workspace-by-link).
