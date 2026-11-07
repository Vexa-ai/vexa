- **"Forget that" removes it everywhere it is kept.** The new `workspace_forget` tool takes files
  (an attachment's `uploads/…` path), a phrase, or every identifier found. It removes them from the
  workspace and rewrites the workspace's git history, so no copy stays reachable. It also redacts
  every stored version of the person's chat transcripts (each turn is kept), chat titles and notes,
  and the recently-opened list. An audit line in the person's private `_system` workspace records
  what kind of item was forgotten and how much, never the content. The tool is called twice: first
  to preview and ask, then with `confirm` after the person says yes. If the workspace is mirrored to
  a repository, that repository keeps its copy until the person agrees to a force-push (`push`).
  Files dropped into a chat (`uploads/`) are no longer committed to workspace history. A page or
  entity write that would hold a government id, a passport, card or bank account number, or a
  credential is refused, and the refusal names the kind, never the value. The same identifiers are
  removed from any file before a turn commits. See [Workspaces](/minutes/workspaces).
