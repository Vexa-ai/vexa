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

## overlay-hit.mjs

The overlay hit-test (guidelines §3.5, §4.11): opens the catalogue's Dialog, ConfirmDialog and
Drawer in a real browser and fails when the element under a dialog button is not that button, when
a mouse click on the confirm does not reach it, or when the drawer's scrim leaves its stratum.
jsdom has no hit-testing, so this cannot be a unit test. Runs in the terminal-layout workflow;
locally, on bbb with `NODE_PATH` pointing at a Playwright install.

## signin-link.mjs

`node scripts/signin-link.mjs [email]` prints a one-time sign-in link (`/api/auth/redeem?t=…`) for
the person named (default `self-host@vexa.ai`). It is the emailed door's own token, minted by the
same rule (`src/app/api/auth/magicTokenMint.mjs`) and redeemed by the same route: verified, used
once, valid for an hour, and admitted only if admin-api says that person may sign in. It is the
first-run door of an install with no mail and no OAuth: Lite prints one at start and ships it as
`vexa-signin-link` (`docker exec vexa-lite vexa-signin-link`). Needs the terminal's
`NEXTAUTH_SECRET` (or `MAGIC_LINK_SECRET`) and `TERMINAL_PUBLIC_URL`; refuses when
`VEXA_SIGNIN_METHODS` leaves out `email`.
