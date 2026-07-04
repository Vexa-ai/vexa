# api/tokens/[id]

`DELETE` — revoke ONE of the logged-in user's own tokens. admin-api's
`DELETE /admin/tokens/{id}` is admin-tier and unscoped, so ownership is enforced HERE, server-side
with the admin key: the id must appear in the token list of the cookie-derived user before the
revoke is forwarded — otherwise 404, indistinguishable from a nonexistent token (no cross-user
probing).
