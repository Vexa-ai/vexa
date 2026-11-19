- **Git remotes on a self-hosted GitLab (#1818).** A repository address may now be nested
  (`https://git.example.com/group/subgroup/repo`) on any host but `github.com`. A token is sent the
  way the host expects: `oauth2:<token>` for a host the operator registers as GitLab in
  `VEXA_GIT_PROVIDERS` (Helm `agentApi.gitProviders`), or `<user>:<token>` when typed that way.
  Publish can create the repository in a GitLab group, and refuses any other host by name. See
  [Repositories on GitLab](/minutes/workspaces#repositories-on-gitlab-or-another-self-hosted-server).
