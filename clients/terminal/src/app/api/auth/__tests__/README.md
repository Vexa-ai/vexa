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
