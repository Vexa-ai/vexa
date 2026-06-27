# auth/login

`POST {email}` — find-or-create the user + mint an APIToken (scopes bot,tx,browser) via admin-api,
set the httpOnly `vexa-token` cookie. No email is sent.
