# surfaces

One contributed module per surface. Each file `registerSurface`s its activity-bar item + view(s) (and
optional `onSubmit`/commands); `index.tsx` is the barrel whose import triggers registration. The shell
(`../workbench/`) renders whatever is registered — adding a surface is a new file + a barrel import,
never an edit to the shell (P2/P6). Real today: `chat` (MVP0), `workspace` (MVP1), `tasks` + `routines`
(MVP2). Placeholders (Live/Inbox/Calendar) keep the activity bar complete until their MVP.
