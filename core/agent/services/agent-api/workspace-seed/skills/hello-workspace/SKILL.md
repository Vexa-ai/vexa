---
name: hello-workspace
description: Example skill proving workspace skills are discovered. Trigger only when the user explicitly asks to "run the hello-workspace skill" or to demo workspace skills. Replace or delete this once you add real skills.
---

# hello-workspace (example)

This is a minimal example skill seeded into every workspace. It demonstrates that
skills placed under `skills/<name>/SKILL.md` are auto-discovered by `claude` (the
governed `skills/` tree is symlinked into `.claude/skills`).

When invoked, simply reply:

> hello-workspace skill is live — workspace skills work.

## Adding your own skill

- Create `skills/<your-skill>/SKILL.md` with YAML frontmatter (`name` + `description`).
- The `description` is what decides when the skill triggers — be specific.
- A skill may include helper scripts alongside `SKILL.md`. Script execution is NOT
  separately gated: it runs under whatever `--allowedTools` the current turn already
  grants (the existing capability gate stands).
