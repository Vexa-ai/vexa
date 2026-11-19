- **Workspaces, people, companies and agents have a face (#1783).** One identity component draws a
  stored image when there is one, else a monogram and colour derived from the id, the same on every
  load and readable in dark and light. It appears in the sidebar, the "Writes to" chip, the page path
  line, hover cards, @-mentions, chat bubbles, the call grid with the camera off and the invite
  screen. A CRM person who does not use Vexa is shown with initials and a company badge, never a
  face. A workspace's image lives in the workspace (`.vexa/icon.svg|png|webp`, named by README
  `icon:`, up to 200 KB, SVGs cleaned of scripts and external references) and defaults to its
  company's logo (README `domain:`). People change it with **⋯ → Change image…**. The agent sets one
  unasked with the new `workspace_icon_set` tool: the company's logo found on its site, or a simple
  designed mark. In a shared workspace that is a proposal the owner accepts in one click.
  `python -m control_plane.backfill_identity --dry-run` lists what it would propose for existing
  workspaces.
  **Rollback:** nothing to undo in a database; an image is a file in its workspace
  (`.vexa/icon.*` and the README's `icon:`), and older versions ignore both.
