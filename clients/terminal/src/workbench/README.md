# workbench

The shell. The parts layout (activity bar · primary sidebar · main · auxiliary · panel · status bar)
and the `LayoutService` that toggles/sizes them, plus the `Composer` that turns `/`-input into commands
and routes plain text to the active surface's `onSubmit`. Surfaces are contributed (see `../surfaces/`),
never hardcoded here — adding a surface is a `registerSurface` call, not an edit to the shell (P2/P6 on
the client). VSCode-inspired: parts + a contribution registry + DI services (`../platform`).
