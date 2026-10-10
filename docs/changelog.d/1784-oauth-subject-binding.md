- **A Google or Microsoft account signs in to one account only (#1784).** The first sign-in through
  a provider binds the account to that identity's stable id (Google's `sub`, Microsoft's tenant and
  object id). A later sign-in that carries the same address but another identity of that provider is
  refused, so inside a pinned Microsoft tenant an administrator cannot reach someone else's account by
  editing a user's email. Existing accounts are bound on their next OAuth sign-in.
