# auth/login

`POST {email}` — find-or-create the user + mint an APIToken (scopes bot,tx,browser) via admin-api,
set the httpOnly `vexa-token` + `vexa-user-info` cookies. No email is sent.

**Development only.** Outside `NODE_ENV=development` this route answers 403 before it reads the
body: production sign-in is the emailed magic link (`../request-link` → `../redeem`) or OAuth. It
used to accept any address containing `test` on any deploy — and, in minutes mode, any address at
all — which made a deployed terminal password-less. What is left is local dev tooling.

To sign in against a deployed container, request a link and redeem it:

```bash
curl -s -X POST https://<host>/api/auth/request-link \
  -H 'content-type: application/json' -d '{"email":"you@example.com","next":"/"}'
# then open the link from the mailbox (dev: Mailpit's JSON API at :8025/api/v1/messages)
```
