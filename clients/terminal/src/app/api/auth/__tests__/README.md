# auth/__tests__

Unit tests for the auth routes:

- `login.test.ts` — direct email login find-or-create against a mocked admin-api, and the
  production gate that makes the route dead outside a development build.
- `magicToken.test.ts` — sign/verify, expiry, the single-use jti ledger, and the `next=`
  open-redirect guard.
- `requestLink.test.ts` — the emailed link's shape, and that the response never distinguishes a
  known address (or a working mailer) from an unknown one.
- `redeem.test.ts` — cookies + 302 on a good link; refusal on replay, expiry, and forgery.
- `instance.test.ts` / `findOrCreateUserToken.test.ts` — the admin-claim probe and the shared mint.
- `signinAllowList.test.ts` — who may sign in (Vexa-ai/vexa#1783): every door refuses an unknown
  address before anything is created or mailed; existing users, admins and allow-listed addresses
  get in; the email form's answer is identical (and equally fast) for allowed and refused
  addresses; an admin-api that cannot answer refuses (fail closed); the first admin claim still
  works, and `VEXA_ADMIN_EMAILS` turns it off.
