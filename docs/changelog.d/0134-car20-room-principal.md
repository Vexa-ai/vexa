- **A room's turn runs as its workspace's own principal, by default (D2).** `VEXA_ROOM_WS_PRINCIPAL`
  is now on. A room's token names the workspace's numeric principal, an account that owns nothing of
  a person's, with the speaker as `on_behalf_of` and the owner as payer. It reads and writes its
  workspace, shows pages on its own room's call, and proposes to the owner's book; it reaches no
  other workspace of the owner's. Set `VEXA_ROOM_WS_PRINCIPAL=false` to run rooms as the owner again.
  See [Shared chats](/minutes/shared-chats).
- **Helm and lite agent-api now talk to identity (`VEXA_ADMIN_API_URL`).** Workspace and closed-room
  membership move from files on the workspaces volume into identity's registry on the first start
  after the upgrade, a room of two's hidden workspace staying hidden; members and owners keep their
  access. Shared workspaces now mount into Helm and lite turns as they do on compose. See
  [Kubernetes](/deployment-kubernetes).
