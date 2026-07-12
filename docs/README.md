# docs/ — where each kind of documentation lives

**The law is published.** The constitutions, protocols, and compliance maps are single-sourced
in [`docs/docs/`](docs/) — the Mintlify site (docs.vexa.ai), nav group **Governance** — and
served to agents as `.md` + `/llms.txt` automatically. Start at
[`docs/docs/governance/index.mdx`](docs/governance/index.mdx) ("How Vexa governs itself").

What remains here, deliberately repo-side:

- [`adr/`](adr/) — the case law: decision records the constitutions cite (`lane:contract`).
- [`views/`](views/) — **generated** architecture projections (`architecture.dsl`, `.mmd`) —
  written by `pnpm arch:dsl` / `arch:viz`, drift-checked by `gate:dataflow`. Never hand-edit.
- [`test-scenarios/`](test-scenarios/) — internal manual test notes.
- `ARCHITECTURE.md`, `DELIVERY*.md`, … — **pointer stubs** kept so historical links (issues,
  PRs, Discord) resolve; the content lives in `docs/docs/governance/`.
