- **The desktop's workspaces, agents and files (#1783, feature `desktop`, off by default).** agent-api
  answers `POST /api/desktop/sections` for the person's own app: every workspace they can open — its
  name, kind, access, members, last change, last call and live call — with hidden ones only counted,
  and the agents and files they placed on the desktop, resolved for them (their own routines; files
  they can open; anything else shows as unavailable). It is read fresh each time, stores nothing and
  calls no model. A colleague's presence follows the new `_global/POLICIES.md` key
  `presence_visibility` (`off` · `shared_chats` · `workspace_members`, default `workspace_members`),
  coarse and never shown for someone who switched theirs off. Contract: `desktop.v1`.
