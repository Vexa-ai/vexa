# Mintlify Docs Site Bundle

This folder is the Mintlify publishing bundle.

- Canonical docs source: `../docs/` (edited by humans, reviewed in PRs)
- Mintlify site output: `./` (generated)

## Workflow

1. Edit content in `docs/`.
2. Regenerate `docs-site/`:

```bash
node scripts/mintlify-sync.js
```

3. Point Mintlify at this folder (GitHub integration), or deploy it as a standalone docs site.

## Why `docs-site/`?

Mintlify expects `docs.json` and MDX pages. We keep `docs/` as plain Markdown so it remains readable in GitHub and easy to maintain.

