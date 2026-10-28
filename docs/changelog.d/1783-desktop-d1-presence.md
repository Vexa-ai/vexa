- **A person's presence switch is an account setting (#1783).** Whether colleagues see someone as
  active (`now` · `today`) is now the `presence` person setting in identity, on by default, beside
  the timezone and mail preferences. It changes with the person's own sign-in only (`PUT
  /user/settings`, or the app's switch through agent-api); a worker acting for them may read it and
  is refused when it changes it. agent-api reads it for everyone on a desktop or people strip in one
  internal read (`GET /internal/presence`), cached for 30 seconds, and shows no presence when
  identity cannot answer. A switch-off recorded before this release is carried over to identity the
  first time the person is looked up, so nobody who opted out reappears. No database migration: the
  setting lives in `users.data`.
