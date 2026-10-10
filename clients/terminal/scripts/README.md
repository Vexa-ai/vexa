# terminal/scripts

[`check-isolation.js`](check-isolation.js) — the app's `gate:isolation` (P2) check.
`@vexa/terminal` is a Next.js app; the check treats relative paths and the `@/*` tsconfig
alias (→ `./src/*`) as intra-package, allows Node/browser builtins, and requires every
bare/npm import to be a **declared** dependency in `package.json` — so the app installs and
builds standalone. Dynamic `import(`${expr}`)` specifiers are skipped (not static).

## layout-sweep.mjs

The terminal's layout proof (design guidelines §8, L1–L3): loads a page that renders the Minutes
shell at 640–1920px in both themes and fails on any pane that scrolls sideways, a conversation
below its mode's floor, a composer toolbar on more than one row, or a tab strip that hides a tab
without its overflow control. Needs Playwright, which is not a terminal dependency — run it on the
sweep runner (bbb) with `NODE_PATH` pointing at an install. `MEASURE` is exported so the same
measurement can run in any browser console.
