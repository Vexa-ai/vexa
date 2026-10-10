# auth/claim-code

`POST {code}` — the first step of claiming an instance nobody has claimed: the visitor types the
one-time admin claim code admin-api wrote to its log at boot. admin-api checks it
(`/internal/admin-claim/check`); a live code is kept in the httpOnly `vexa-claim-code` cookie
(path `/api/auth`, one hour), so the sign-in that follows carries it to admin-api, which admits that
sign-in and makes it the administrator. A wrong code is refused here, at once (`403`).

No code, no claim: without it a fresh instance admits nobody new, and the claim itself
(`/internal/bootstrap-admin`) refuses. The code works once.
