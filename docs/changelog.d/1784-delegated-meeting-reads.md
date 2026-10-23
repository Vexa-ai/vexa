- **A scheduled or event-triggered agent run reads only the meetings its workspaces allow (#1784).**
  Identity now answers a worker's delegation token with only the person's shared-workspace memberships
  that fall inside the run's workspace set, so the meeting tools list and read a colleague's meeting
  only when it is bound to one of those workspaces. A chat with the person present is unchanged.
