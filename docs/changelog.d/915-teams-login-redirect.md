- **Teams: a join redirected to the Microsoft sign-in page now fails fast and says so (#915).** When
  an anonymous Teams join was bounced to `login.microsoftonline.com`, the bot spent ~75s hunting for
  pre-join controls that a sign-in page does not have and then reported
  `Bot was not admitted into the Teams meeting within the timeout period` — an admission timeout for
  a meeting no host was ever asked to admit it to. The join now checks where the navigation actually
  landed and terminates there with a typed reason, `teams_auth_redirect: … (url=…)`, carried into
  `last_error`. Failures of this shape are legible in bot logs and the meeting record instead of
  being bucketed as host behaviour. A pre-join that is merely slow on a Teams host is unchanged.
