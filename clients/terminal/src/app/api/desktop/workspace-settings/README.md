# terminal/src/app/api/desktop/workspace-settings — the person's own settings about each workspace

`GET` reads and `POST { workspace, settings }` writes the per-person settings on the CRM `member_of`
relation (pinned, hidden from home, muted, notification level, room order, level, collapsed sections),
through crm-api's `desk/workspaces/read|set` with the signed-in person's own key (#1783, R3 3.4). crm-api
is the only writer; nothing is kept here or in the browser.
