# auth/company

`POST {to: "company" | "me"}` — the Company profile switch (ADR-0049). An instance administrator
(`is_admin` from admin-api's oracle) puts the deployment's admin account session on this tab, and
takes it off again, without signing in twice.

- `to: "company"` — refused unless the caller's token validates as `is_admin` and is not the admin
  account. The admin account is the address `VEXA_ADMIN_ACCOUNT` names (created through admin-api's
  `POST /admin/users` the first time, which is where admin-api stamps it); a token is minted for it
  named `company:<person id>`, for 12 hours, and it is used only when the oracle answers
  `admin_account: true` for it (otherwise it is revoked). The person's own token waits in the
  httpOnly `vexa-person-token` cookie.
- `to: "me"` — the person's own token is validated and restored; the company token is revoked.

`../me/` reports `company: {person}` while a tab works as the company and `can_work_as_company` for
an administrator who may switch; a Company session that ran out returns the person to their own.
`../logout/` ends both. Cookie names and the address list live in `../companyMode.ts`.
