- **A new person's first chat works, and "create a workspace and share it" works from the chat.**
  A worker acting for a person can read their settings and workspace regime. Before, every such
  call answered `403 Invalid API Key`. A worker with nobody in the chat can still read a setting
  but cannot change one. `personal` names the person's own workspace on reads too. `workspace_new`
  addresses a named workspace by its name (`eugenia-dmitry`). Inviting someone to one of your own
  private workspaces makes it shared, with you as owner. An invite with no role makes the person a
  contributor. Every workspace tool accepts the name the Files pane shows; when two workspaces
  answer one name, the agent asks which. The `workspaces` tool lists every workspace with its name,
  kind and role, and flags an empty one that has the same name as another. New tools:
  `workspace_rename`, and `workspace_remove` (only after the person confirms). For
  `entity_upsert`, a link field such as `who` can be a sentence, and each name in it is linked; an
  array argument sent as its JSON text is read as the array; and the answer names the organisation
  template file it used (`_global/kg/templates/<kind>.md`), which it used to report as
  `"template": "company"`. See [Workspaces](/minutes/workspaces).
