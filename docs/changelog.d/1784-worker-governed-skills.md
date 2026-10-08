- **The agent worker runs no hooks, and a workspace's skills cannot grant tools (#1784).** Claude
  Code in the worker runs with every hook disabled. It loads the platform's skills together with the
  workspace's own `skills/` folder; a workspace skill loads without its `allowed-tools` and `hooks`
  fields, so it cannot let the agent use a tool the turn was not given. When a workspace skill has
  the same name as a platform skill, the platform's loads. A workspace's `CLAUDE.md` still loads as
  before. The worker image moves to Node.js 22, the version the pinned Claude Code release requires.
