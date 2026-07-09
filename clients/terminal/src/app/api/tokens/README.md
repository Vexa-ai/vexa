# api/tokens (self-serve API tokens)

`GET` lists the logged-in user's API tokens; `POST {scopes[,name,expiresIn]}` mints one (the
secret crosses to the client ONCE, in the 201 response, and is never retrievable again).
admin-api's token endpoints are ADMIN-tier, so these routes run server-side with the
`VEXA_ADMIN_API_KEY` (the `/api/auth/login` idiom) and scope EVERY operation to the user_id
resolved from the httpOnly auth cookies (`currentUser.ts`: `vexa-user-info` email →
`findUserByEmail`) — a user_id from the client is never accepted (P20). Responses are
`no-store`; scopes are validated against the bot · tx · browser set.
