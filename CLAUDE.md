# Workspace — your durable memory

This directory (your current working directory) is your ONLY durable memory, and it is a
git repo that is committed automatically after every turn. Anything you should remember —
facts about the user, knowledge, tasks, notes, decisions — MUST be saved as files here,
under this workspace.

- Save knowledge/notes as markdown files in this workspace (e.g. `notes/`, `kg/entities/`).
- To recall something, READ the files in this workspace.
- NEVER write memory to `~/.claude` or any path outside this workspace — that is ephemeral
  and will be lost. Always use paths relative to this workspace directory.
