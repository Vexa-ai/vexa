# vexa — agent instructions

Read [AGENTS.md](AGENTS.md) (the working protocol) and
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (the principles the gates enforce)
before editing. Multiple agents may share this checkout — prefer worktrees for
multi-file edits, and run `node scripts/gates.mjs` before pushing.

Layout: pnpm + turbo monorepo — `core/` (gateway, agent, runtime), `clients/terminal`
(Next.js 15 web client, port 3000, `npm run dev`), `docs/` (Mintlify), `calm/`
(FINOS CALM model). Licensing is FINOS-gated: new deps must be Category A
(MIT/BSD/Apache); weak-copyleft needs an entry in `license-exceptions.json`
(see ADR-0004) — never add GPL/AGPL.
