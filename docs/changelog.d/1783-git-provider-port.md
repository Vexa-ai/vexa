- **Workspaces sync with a self-hosted GitLab as well as GitHub (#1783).** An operator lists git
  providers in `agentApi.gitProviders` — `{name, kind: gitlab|github, baseUrl, caBundle?}` — and each
  becomes a place workspaces attach from, push to and publish on, nested GitLab groups included
  (`it/ai/vexa/notes`), with its own private CA. Each person saves one token per provider in
  Connections → Git (kept in the credential broker, sent only to that provider). The publish form
  picks the provider and the group, and the agent can publish from the chat (`git_providers`,
  `workspace_publish`); a rejected token is a typed refusal naming the provider. GitHub behaves exactly
  as before. See [Use a self-hosted GitLab](/minutes/self-hosted-gitlab).
