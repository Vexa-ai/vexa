- **A worker stays inside the workspaces its dispatch was given, on every route (#1784).** An agent
  worker dispatched without a person in the chat carries the exact set of workspaces its routine or
  event may touch. agent-api now refuses (`403`) any route that names a workspace outside that set,
  checked where the workspace is resolved rather than route by route, including identity, activate,
  swap, import, rename, sharing, invites, members, reset, the chat target and the desk touch.
  Publishing, pushing, pulling and detaching a Git remote also need a person in the chat. A generated
  test calls every route that names a workspace as such a worker.
