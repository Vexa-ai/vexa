# workbench

The shell. The parts layout (activity bar · primary sidebar · main · auxiliary · panel · status bar), plus the `Composer` that turns `/`-input into commands
and routes plain text to the active surface's `onSubmit`. Surfaces are contributed (see `../surfaces/`),
never hardcoded here — adding a surface is a `registerSurface` call, not an edit to the shell (P2/P6 on
the client). VSCode-inspired: parts + a contribution registry + DI services (`../platform`).

The workbench sits ABOVE `surfaces/`: `Workbench.tsx` composes the registered surfaces and the
right-rail chat, and no surface imports it. The `LayoutService` surfaces open tabs through lives in
`../platform/layout.ts`, and the chat transcript renderer in `../surfaces/agent-window.tsx`
(both moved out of this folder in Vexa-ai/vexa#1783).
