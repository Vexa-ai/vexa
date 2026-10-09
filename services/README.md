# services/

Top-level services that sit outside the `core/` domains.

- **`dashboard/`** — the hosted dashboard as it runs at dashboard.vexa.ai, brought from `main` to be
  retired ([`RETIRING.md`](dashboard/RETIRING.md)). It is outside the pnpm workspace, and its own
  `.gateignore` takes that tree, and only that tree, out of the per-dir gates (ADR-0007). Nothing in
  the product builds or references it.
