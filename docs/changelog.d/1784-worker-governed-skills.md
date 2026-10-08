- **The agent worker loads only the platform's skills and runs no hooks (#1784).** Claude Code in the
  worker now reads skills from the seed shipped in the image, not from a workspace's own `skills/`
  folder, and runs with every hook disabled. A workspace's `CLAUDE.md` still loads as before. The
  worker image moves to Node.js 22, the version the pinned Claude Code release requires.
