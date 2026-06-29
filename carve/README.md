# carve/ — open-core publishing toolkit

Publishes the open-core subset of this monorepo to **Vexa-ai/vexa-core** reproducibly.

| File | Role |
|---|---|
| `manifest.sh` | **Single source of truth** — INCLUDE/EXCLUDE paths, mailmap, overrides, transforms. Edit this to change what's contributed. |
| `seed.sh` | **One-time** full-history seed (`git filter-repo`, author+DCO preserving). Force-pushes. Use only before external contributors exist. |
| `sync.sh` | **Ongoing** append-only sync: replays new mono commits since `.carve/checkpoint` as a PR, preserving original authorship. Never rewrites history. |
| `_apply_layer.sh` | Shared override layer (carve-owned files + transforms + docs). |
| `overrides/` | Carve-owned files that replace the mono's (e.g. compose-only `Makefile`). |
| `transform.sh` | Deterministic edits (e.g. strip internal `package.json` scripts). |
| `mailmap.txt` | Placeholder identities → real contributors. |

## Lifecycle
1. **Seed once:** `carve/seed.sh --push` → history-preserving initial `vexa-core`.
2. **Thereafter:** `carve/sync.sh --push` → opens a PR with new work; merge it. Safe alongside external PRs.
   **⚠️ Merge sync PRs with a MERGE COMMIT (`gh pr merge --merge`), never `--squash`** — squash collapses the replayed commits and re-attributes them to the merger, erasing original contributor authorship (the whole point of sync). Best to disable squash-merge in the repo settings.

The checkpoint (`.carve/checkpoint`, a mono SHA) lives committed inside vexa-core; each sync reads it and advances it.
