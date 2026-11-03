- **Workspace state follows the signed-in account (#1783).** What the terminal keeps in the browser —
  the open workspace, a pending meeting or preset, the composed view, layout — is stored per account,
  and state left by a different account on the same browser is cleared rather than applied. A group
  workspace can be switched on in your active set only while you are a member of it. A workspace you
  are not a member of answers `not-yours` with no name, and the chat header says "Writes to" a
  workspace only when you can write there. The same holds for a link resolved by a delegated
  agent whose reach is narrower than yours. An invite link that has expired, been used or been
  withdrawn says only that. See [Workspaces](/minutes/workspaces).
