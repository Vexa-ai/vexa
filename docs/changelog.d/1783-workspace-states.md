- **Workspaces have states: active, inactive, hidden — set from the chat.** Hidden workspaces are
  not mounted, not listed in Files or pickers, and kept with their history; unhide makes them
  active again. Hiding a shared workspace hides it for you only. Each chat has its own reads and one
  target, and the chat header shows them as one line — "Writes to X · Reads N workspaces" — with the
  full parameters on hover; everything is changed in the chat. Chat profiles — work, study, hobby,
  shared work (bound to a workspace) and incognito — are presets of that scope; the default profile
  applies to new chats. A new chat writes to your default from its first message. Same-named
  workspaces are named apart. A chat can have no workspaces. `workspace_pull` can merge when both sides
  moved. An owner can delete a shared workspace; when others are in it, the agent
  names them first and nothing is deleted until you confirm. Your personal workspace can't be
  hidden, shared or deleted. New tools: `workspace_list`, `workspace_state_set`,
  `workspace_defaults_set`; `workspace_remove` takes `confirm`. Opening a chat no longer switches
  your shared workspaces on and off. The gateway now waits a few seconds for a service that is being
  replaced instead of answering 502 at once. See [Workspaces](/core/workspaces).
