# terminal/src/app/api/desktop/attention — `POST /api/desktop/attention`

What needs the person and what is working where (#1783, R3, feature `home2`, decision E1). Reads the
cards on the person's desk from crm-api (`desk/read`, only the fields the rules use), then asks agent-api's
rule-based `desktop/attention` with them. Both with the signed-in person's own key; nothing stored, no
model call. Body `{ workspace?: <slug> }`: one workspace's richer sections for the workspace page, or every
workspace for the home. A crm that refuses or is down costs the card items only.
