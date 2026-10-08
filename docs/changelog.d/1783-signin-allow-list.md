- **Unknown addresses can no longer create accounts (#1783).** Every terminal sign-in (emailed link,
  Google, Microsoft) is now admitted only for an existing user, an admin, or an address on the new
  sign-in allow-list: `VEXA_SIGNIN_ALLOW` on admin-api (exact addresses and `@domain` entries) plus
  what the admin adds under Settings → Sign-in. Anybody else is refused before an account exists, and
  the email form gives the same answer either way. Accounts that already exist keep signing in, so
  upgrading locks nobody out. If admin-api is unreachable, new sign-ins are refused. See
  [Who may sign in](/deployment#who-may-sign-in).
- **The first administrator needs a one-time claim code (#1784).** On an instance nobody has
  claimed, admin-api writes an admin claim code to its log at boot; the terminal's claim screen asks
  for it, and only the sign-in that carries it is admitted and becomes the admin. The first visitor to
  an exposed instance can no longer take it. `VEXA_ADMIN_EMAILS` is now read by admin-api (compose
  `.env` unchanged; Helm `adminApi.adminEmails`, with a value still under `terminal.extraEnv` carried
  over) and names the admins instead, with no code. A configured admin list or allow-list closes the
  unclaimed door entirely.
