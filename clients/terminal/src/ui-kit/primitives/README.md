# ui-kit/primitives — the components that own the look

**Concern.** Terminal design guidelines §4: every visual decision about a control lives in a
primitive, so surfaces decide *what* appears and *where*, never colour, size, radius or state.

**Surface (exported only through `src/ui-kit/index.tsx`, P6).**
- `Menu` — WAI-ARIA menu button: arrows, Home/End, typeahead, Enter/Space, Esc returns focus.
- `KeyValue` (+ `kvValue`, `humanizeKey`) — the metadata table; container-query stacked below
  360px of pane width; values rendered by type; never breaks a word.
- `Truncate` — one line, end or middle ellipsis, full value in the tooltip and accessible name;
  `http:`/`https:`/`mailto:` links only.
- `DateText` — a date as one `nowrap` `<time>`, through `../format/date.ts`.
- `Fold` — two forms of one control, switched by a container query at 280/360/400/520/560px.
- `OverflowStrip` — a scrolling tab row with fade edges and an overflow menu of every tab.
- `Button`, `IconButton` (label required, `pressed` toggles), `Spinner`, `StatusDot`, `Kbd`.
- `Badge` (static), `Tag` (static), `Chip` (interactive), `EntityChip` — never the same shape.
- `Input`, `Textarea` (label above, error linked by `aria-describedby`), `Select` (a Menu).
- `Tabs` (WAI-ARIA, roving tabindex), `Breadcrumb` (middle collapses into a menu).
- `ListRow`, `PanelHeader`, `Card`.
- `Dialog`, `ConfirmDialog` (replaces `window.confirm`; the button names the act).
- `EmptyState`, `Skeleton`, `ErrorState` (typed fault + Details), `Tooltip`, `Popover`, `Toaster`/`toast` (sonner).
- `ExternalLink` (http/https only, `noopener noreferrer`), `Code`, `SecretReveal` (masked, never in an attribute).
- `SourceList` (an agent's sources; a date shown once).
- `primitives.css` (layout-adjacent primitives) and `controls.css` (controls) — their styles: global, `vx-` prefixed, reading only the terminal's CSS
  variables, imported once through `../styles.css` (Next's App Router admits global CSS only from
  the root layout).

**Dependencies.** React, `lucide-react` (ISC) for icons, `sonner` (MIT) for toasts — both already in the lockfile. No service, no surface.
