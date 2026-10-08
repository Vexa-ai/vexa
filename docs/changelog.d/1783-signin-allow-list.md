- **Unknown addresses can no longer create accounts (#1783).** Every terminal sign-in (emailed link,
  Google, Microsoft) is now admitted only for an existing user, an admin, or an address on the new
  sign-in allow-list: `VEXA_SIGNIN_ALLOW` on admin-api (exact addresses and `@domain` entries) plus
  what the admin adds under Settings → Sign-in. Anybody else is refused before an account exists, and
  the email form gives the same answer either way. Accounts that already exist keep signing in, so
  upgrading locks nobody out. If admin-api is unreachable, new sign-ins are refused. While no admin is
  claimed the first sign-in still becomes the admin: set `VEXA_ADMIN_EMAILS` before first boot on any
  terminal reachable from outside. See [Who may sign in](/deployment#who-may-sign-in).
