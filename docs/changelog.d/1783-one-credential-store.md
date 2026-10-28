- **Git credentials move to the credential broker (#1783).** Saved GitHub tokens and workspace deploy
  keys are kept in the credential broker, the same store as Connections; agent-api no longer keeps a
  store of its own. Compose and the chart wire this by default. Credentials saved by an earlier release
  move on their owner's next Git operation, or all at once with the broker's one-shot import; keep
  `VEXA_SECRETS_KEY` set until agent-api's boot log no longer counts any waiting, then remove it.
  Both read only regular files (symbolic links are skipped), and an old copy is removed as soon as the
  broker holds that name.
  Without a broker, saving a Git credential answers `503` (`git_credentials_unavailable`); a token
  typed for one operation still works. `VEXA_ENV` is no longer read by agent-api. See
  [Connections → Git credentials](/connections#git-credentials).
