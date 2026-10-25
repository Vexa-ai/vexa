- **A Claude or Codex subscription token no longer reaches agents unless the operator allows it.**
  `VEXA_ALLOW_SUBSCRIPTION_TOKEN_PROXY` is off by default: a turn on a subscription route
  (`CLAUDE_CODE_OAUTH_TOKEN`, the claude CLI's credentials file, Codex's `auth.json`, or the
  catalog's `auth: subscription`) is refused with a message naming the setting, and the runtime
  mounts no subscription file into a worker. API-key routes are unaffected. An operator whose install
  serves only the subscription's own holder can set it to `true` (Helm
  `models.allowSubscriptionTokenProxy`). See [Model credentials & licensing](/model-credentials-licensing).
