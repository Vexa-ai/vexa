- **A member removed from a shared workspace loses it at once.** Their running agent turn is stopped
  within half a second, what it wrote into that workspace is put back, and their agent's worker ends,
  so it no longer holds that workspace's tree (an idle worker ends within 2 seconds). The next message
  starts a worker on what the roster now grants. The same happens to a member made a viewer of a
  workspace their agent writes. See [Workspaces](/core/workspaces).
