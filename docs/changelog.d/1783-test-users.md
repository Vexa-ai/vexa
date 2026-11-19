- **app.dev: sign in as test users without OAuth or email.** With `VEXA_DEV_TEST_USERS=1` (compose
  only), an instance administrator's account menu has *Test users…*: list and create
  `tester-<name>@example.test` accounts and copy or open a one-time sign-in link (15 minutes, single use)
  that goes through the ordinary redeem and admission. Off everywhere else; refused for non-admins and
  for any address outside `example.test`. New admin-api route `GET /admin/users/test-users`.
