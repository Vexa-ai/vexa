# app/design/sections — catalogue sections that are more than one registry entry

`Tokens.tsx` — every colour role with its contrast computed in the page (the gate's own function),
the type scale, radii, shadows and spacing. `Shell.tsx` — `FixtureShell`, the real layout
machinery (`useShellLayout`, `Splitter`, `Sheet`, `Drawer`, `Fold`, `OverflowStrip`) around
placeholder panes, with its own storage key so it never touches the reader's layout; and
`ShellSection`, the same in a box with a width slider. Depends only on the ui-kit front door and
`../fixtures`.
