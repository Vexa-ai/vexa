# vexa-core — agent instructions

**Before editing anything, read [HANDOVER.md](HANDOVER.md) and follow its
protocol.** Multiple agents work in this same checkout without worktrees:
claim files there before multi-file edits, update it as you go, and record
conflicts/resolutions there. Do not commit HANDOVER.md into PRs.

Layout: pnpm + turbo monorepo — `core/` (gateway, agent, runtime), `clients/terminal`
(Next.js 15 web client, port 3000, `npm run dev`), `docs/` (Mintlify), `calm/`
(FINOS CALM model). Licensing is FINOS-gated: new deps must be Category A
(MIT/BSD/Apache); weak-copyleft needs an entry in `license-exceptions.json`
(see ADR-0004) — never add GPL/AGPL.
