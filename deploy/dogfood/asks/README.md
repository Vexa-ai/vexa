# asks — the emailed-link preset library (seed)

The SOURCE for `_global/asks/*.md` on a deployment. Each file is one preset an emailed
`?ask=<name>` link (or an empty-chat chip) can open: frontmatter names the label and the mount
set, the body is the opening prompt, `{{meeting}}/{{ws}}/{{today}}` substitute at click time.

These are SEEDS: the live copies live in the `_global` workspace (admin-owned, hot — editing
them there changes every future click, no rebuild). This directory exists so a container rebuild
cannot silently kill every emailed link — before it existed, the presets lived only inside the
agent-api container. Deploy = copy into `/workspaces/_global/asks/`.

The URL carries a NAME, never prompt text; names match `^[a-z0-9][a-z0-9_-]{0,63}$`.
