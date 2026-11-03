- **Agent workers: the model's tools run as their own user, and only agent-api can message a worker
  (#1784).** Where the worker runs as root, the Claude Code and Codex CLIs — and so every tool the
  model runs — now start as `vexa-tools` (uid 10001, created in the worker image), and the worker
  gives that user only the workspaces a turn may write. A worker built from your own image needs the
  same user, or runs its tools as itself. Every message agent-api puts on a live worker's input
  stream is signed with a key of that worker's own; the worker runs nothing else from that stream.
