- **A Claude or Codex subscription token no longer reaches agents unless the admin allows it.**
  The new company policy `subscription_token_proxy` in `_global/POLICIES.md` is `deny` by default: a
  turn on a subscription route (`CLAUDE_CODE_OAUTH_TOKEN`, the claude CLI's credentials file, Codex's
  `auth.json`, or the catalog's `auth: subscription`) is refused with a message naming the policy,
  and no worker is given a subscription file. API-key routes are unaffected. On an install that serves
  only the subscription's own holder, the admin account sets `subscription_token_proxy: allow`. See
  [Model credentials & licensing](/model-credentials-licensing).
