# terminal/scripts

[`check-isolation.js`](check-isolation.js) — the app's `gate:isolation` (P2) check.
`@vexa/terminal` is a Next.js app; the check treats relative paths and the `@/*` tsconfig
alias (→ `./src/*`) as intra-package, allows Node/browser builtins, and requires every
bare/npm import to be a **declared** dependency in `package.json` — so the app installs and
builds standalone. Dynamic `import(`${expr}`)` specifiers are skipped (not static).
