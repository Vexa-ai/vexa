# Tests

```
features/      — Features: DoD tables define what must be true
tests/*.md           — Nodes: verify, fix, own scripts and feature DoDs
tests/graphs/*.md    — Graphs: compose nodes into validation sequences
```

## Features

Each `features/*/README.md` has a DoD table with weighted checks, ceiling/floor, and a Tests column listing which nodes prove each item. Features define WHAT. The Tests column is the many-to-many link.

## Nodes

Each `tests/*.md` is a node. Nodes are:

- **Sequential** — steps run in order, each depends on the previous
- **Verifiable** — they execute, not just describe
- **Modular** — nodes declare `requires:` to import other nodes
- **Atomic via scripts** — `.sh` scripts automate operations. The `.md` owns the `.sh`
- **Owners** — own their scripts, own feature DoD items, update feature docs and codebase when reality changes

## Graphs

Each `tests/graphs/*.md` composes nodes into a sequence for a specific goal. Graphs pick which nodes, in what order, with what parameters. They don't define new procedures.

## Logging

| What | Where | When |
|------|-------|------|
| Action log | `test-log.md` | During execution (`test-lib.sh`) |
| DoD results | `features/*/README.md` | After graph REPORT step |

## Rules

See [RULES.md](RULES.md).
