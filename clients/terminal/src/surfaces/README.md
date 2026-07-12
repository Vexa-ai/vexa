# surfaces

One contributed module per surface. Each file `registerSurface`s its activity-bar item + view(s) (and
optional `onSubmit`/commands); `index.tsx` is the barrel whose import triggers registration. The shell
(`../workbench/`) renders whatever is registered — adding a surface is a new file + a barrel import,
never an edit to the shell (P2/P6). Real today: `chat` (MVP0), `workspace` (MVP1), `tasks` + `routines`
(MVP2). Placeholders (Live/Inbox/Calendar) keep the activity bar complete until their MVP.

**`workspace.tsx` is the live-collaboration surface** — the KNOWLEDGE panel (full workspace tree, the
`_system` key toggle, cross-workspace search), per-mount sections, the aggregated SOURCE CONTROL
RECENT ACTIVITY feed (email-attributed, clickable files), the "new updates" nav badge
(`updatesBadge.ts` + the Workbench poll), doc auto-reload + one-click **Changes** diff, README
auto-pin on shared connect, and the Share/invite dialog (single-rank). The end-to-end model is in
**[`docs/docs/core/workspaces.mdx`](../../../../docs/docs/core/workspaces.mdx)**.
