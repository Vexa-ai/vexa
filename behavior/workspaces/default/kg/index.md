# Knowledge graph

This directory is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/tree/main/okf) v0.1 bundle: the agent's durable knowledge as markdown files with YAML frontmatter.

- [entities](entities/index.md) — typed entities (`kg/entities/<type>/<slug>.md`); grows as the agent
  records real knowledge. Ships EMPTY — the shape of each type lives in [templates](templates/README.md),
  expressive skeletons that are never read as knowledge and can never leak into a brief.
