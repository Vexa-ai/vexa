- **Generic OIDC sign-in requires a verified email by default.** A token must carry
  `email_verified: true` unless the operator sets `VEXA_OIDC_REQUIRE_EMAIL_VERIFIED=0`. Providers
  that send no such claim, such as ADFS, need that setting. A CRM Person is claimed or matched only
  by an address a sign-in verified: through an emailed link, Google, Microsoft, or an OIDC provider
  that vouched for it. See [OIDC sign-in](/sign-in-oidc).
- **Sealed CRM facts need their own secret.** Set `VEXA_FACT_SEAL_SECRET` to the same value on
  flows-api and the crm outbox. There is no fallback to `INTERNAL_API_SECRET`. A seal now covers
  the event it is posted with and expires five minutes after sealing.
- **In a room of two, the person who sends the message pays for the turn.** Spend caps and usage
  follow that person. Ordinary rooms still bill the workspace owner.
