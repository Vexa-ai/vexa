- **A dev or demo instance can open sign-in to everyone, explicitly (#1784).** `VEXA_SIGNIN_ALLOW=*`
  (the entry `*` alone) admits any address. Nothing sets it by default, so an instance stays closed
  unless its operator writes it. admin-api logs a warning at boot while it is set, `*` is never a
  pattern inside an entry, and `VEXA_ADMIN_EMAILS` refuses it. See
  [Who may sign in](/deployment#who-may-sign-in).
