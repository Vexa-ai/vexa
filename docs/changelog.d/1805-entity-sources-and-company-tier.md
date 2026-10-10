- **An entity page's source keeps its commas (#1805).** A source such as "Zoom call 17, 2026-10-09
  14:07 UTC" is recorded once instead of being split and duplicated on every update; pages split
  that way are repaired the next time the same source is recorded. Agents that are not org admins
  now write company pages on their own desk instead of being refused by `_global`, and the
  assistant's own model name is no longer proposed as a page. See [Workspaces](/core/workspaces).
