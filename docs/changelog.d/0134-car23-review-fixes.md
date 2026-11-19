- **Meeting Canvas views run in an isolated frame.** A workspace-written canvas view now runs in
  `/canvas-frame`, a sandboxed document with an opaque origin that connects nowhere; the terminal
  page posts it the meeting and runs only the actions it posts back. It can no longer reach the
  signed-in person's session.
- **No anonymous visitor acts as a deployment key.** The terminal forwards a request with no
  session with no key, so the gateway refuses it. Lite keeps opening signed in through
  `VEXA_TERMINAL_ZERO_LOGIN=true` (its default); set it to `false` when port 3001 is reachable by
  others. See [Lite](/deployment-lite).
- **Ownership doors always need the internal secret.** `DEV_MODE` no longer opens admin-api's
  internal routes that change the admin role, a membership or a platform setting, or that read one
  named person's data.
- **Shared workspaces:** a viewer can no longer set a workspace's purpose; an instance admin's chat
  no longer redeems someone else's scaffold; and chat history never reads another member's legacy
  thread out of a shared workspace.
- **Policies fail closed.** When `_global/POLICIES.md` exists but cannot be read, or names an
  unknown profile, flows applies the closed value of every rule that widens who is mailed, joined
  or read, and logs it at warning.
- **Background jobs are bounded.** A job cannot start another job, and the agent's own
  `spawn_job` is refused past four open jobs.
- **Adoption panel:** Grafana logs in as a SELECT-only `vexa_reporting` role
  (`adoptionPanel.readerSecret`, required); the chart refuses the owner credentials.
