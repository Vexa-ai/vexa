# app/design — the design catalogue (`/design`)

**Concern.** Every design token and ui-kit primitive as it really renders, in dark and light side by
side and at pane widths, plus the responsive shell around placeholder panes (terminal design
guidelines §9; `docs/docs/governance/ui-design.mdx`).

**Surface.**
- `page.tsx` — the route. Returns 404 in a production build unless `VEXA_TERMINAL_DESIGN_CATALOGUE=1`
  (`gate.ts`); `noindex, nofollow`; analytics skip it; no sign-in, because it holds no user data.
- `registry.tsx` — one entry per ui-kit component, each with a fixture demo. **Adding a primitive
  means adding its entry here** — `designCatalogue.test.ts` (G13) fails otherwise.
- `Frame.tsx` — dark and light frames (each sets its own `data-theme`), optional pane widths.
- `sections/` — the token section (live contrast ratios) and the fixture shell.
- `fixtures.ts` — placeholder data only ("Person Name", "Example Company").
- `?view=shell` renders only the fixture shell, full screen — the layout sweep's target.

**Dependencies.** The ui-kit front door, `app/tokens.css` (through the root layout) and its own
files — nothing else: no API client, no session, no `next-auth` (G11, `designCatalogue.test.ts`).
