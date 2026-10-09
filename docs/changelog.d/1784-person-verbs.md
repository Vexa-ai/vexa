- **Destructive and sharing verbs need a person in the chat (#1784).** A worker dispatched without a
  person in the chat is now refused deleting, resetting or archiving a workspace, changing who it is
  shared with (invites, members, roles, leaving, sharing and un-sharing), saving a Git token or a
  deploy key, loading a repository into a workspace, recording a verdict on a proposed claim, and
  cancelling a routine, as well as the verbs that already needed one. The list is the `verbs` rows of
  `core/agent/routes.v1.json`, and agent-api applies it in one place.
