- **Per-person workspace settings live on the CRM `member_of` relation (#1783).** With `desktop` on,
  crm-api keeps each person's own pinned, hidden-from-home, muted, notification level, room order,
  level override and collapsed sections per workspace. New tools `workspace_settings_read` and
  `workspace_settings_set`; the gateway fronts `/crm/desk/workspaces/read` and `/crm/desk/workspaces/set`.
  Only the person, or their agent in their own one-person chat, changes them. The CRM schema moves to
  version 5 (one new table; existing rows untouched). Off by default.
