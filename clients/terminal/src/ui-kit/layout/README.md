# ui-kit/layout — the responsive shell

**Concern.** How the Minutes shell's three panes — the rail (chat list), the conversation and the
pages panel — share the window at any width. Terminal design guidelines §3
(`docs/docs/governance/ui-design.mdx`).

**Surface.**
- `shellLayout(viewportWidth, prefs, storedWidths)` — pure. Returns the mode (`wide` ≥1440,
  `desktop` ≥1200, `compact` ≥960, `narrow` ≥720, `single`), each pane's form
  (`railKind`: docked / strip / drawer; `pagesKind`: docked / collapsed / sheet / fullscreen), the
  grid columns, the widths and the splitter bounds. The conversation never drops below the mode's
  floor (560 / 480 / 440); the rail collapses first, then the pages panel.
- `useShellLayout(rootRef, { legacy })` — the ONE writer of `localStorage["vexa.shell.v1"]`
  (`{ prefs: { railOpen, pagesOpen }, widths: { <mode>: { rail, pages } } }`). Measures the shell,
  keeps the reader's preferences apart from what fits (an auto-collapse never writes a
  preference), remembers widths per mode (clamped on read, never discarded), and owns the transient
  drawer/sheet state.
- `Splitter` — `role="separator"`, keyboard (←/→ 16px, Shift 64px, Home/End, Enter resets),
  pointer capture, 8px hit target.
- `Sheet` / `Drawer` — one element with three forms (`inline` = `display: contents`, `overlay`,
  `fullscreen`), so a pane never remounts when the window crosses a breakpoint. Modal while open:
  focus moves in, Tab is trapped, Esc and the scrim close it, focus returns to the trigger.
- `layout.css` — `.vx-pane` (a pane root: inline-size container, no sideways scroll), the
  splitter, sheet, scrim and rail-strip styles. Imported once through `../styles.css`.

**Dependencies.** React only. No service, no surface: `minutes/MinutesShell.tsx` renders what this
folder returns. Imported through the ui-kit front door (`src/ui-kit/index.tsx`).
