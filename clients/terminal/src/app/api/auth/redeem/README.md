# auth/redeem

The back half of the magic-link door. **Prefetch-safe:** Microsoft 365 (Teams `SkypeUriPreview`,
Defender Safe Links) and other mail scanners fetch a link before the person clicks it, so the link
itself never spends the token (live 2026-10-15).

- `GET ?t=<token>&next=<relative-path>` — verifies signature and expiry **without consuming
  anything** and renders a small page: "Sign in to Vexa as m…@domain" (masked) with one Sign in
  button (a form POST, no script). Expired/forged links and a closed email door are refused here.
- **Bound to the browser that asked.** Asking for a link (`../request-link`) sets the httpOnly
  `vexa-signin-bind` cookie on that browser, and the link carries its SHA-256. Opened in any other
  browser the GET says so with no button, and the POST answers `403` before anything is spent — so
  a link somebody asked for to their own address cannot sign a victim into their account (login
  CSRF, S03T-3). Only a reserved-domain test user's link (`../test-users/link`) is unbound.
- `HEAD` — `200`, empty, no side effect.
- `POST` (the button; form fields `t`, `next`) — the only act:
  0. Same-origin only: `Sec-Fetch-Site` (when sent) must be `same-origin`, `Origin` (when sent)
     must be this instance (`TERMINAL_URL` / `NEXTAUTH_URL` / the forwarded host); a POST with
     neither is refused `403` (login-CSRF guard).
  1. Verifies the HMAC signature and expiry and **burns the jti** (single use), before any
     admin-api round-trip, so a replay cannot race a slow mint.
  2. Runs the same `findOrCreateUserToken` + cookie flow as every other door, so downstream
     consumers see an ordinary session.
     That function first asks whether the address may sign in AT ALL (Vexa-ai/vexa#1783) — checked
     again here because the link may outlive the allow-list entry that sent it — and refuses before
     any account exists: `403` with the shared sentence, or `503` when admin-api cannot answer.
  3. `303`s to `next`, already reduced to a site-relative path — an emailed link can never bounce
     its recipient off this origin.

A refused link answers with a small self-contained HTML page (410 used/expired, 400 forged,
403 another browser's, 503 unconfigured), not a JSON error: the visitor arrived by clicking a mail.
Every card carries its own policy: nothing runs, its one stylesheet applies by hash, and its form
posts only back here.

Other one-time links were checked and are already safe: workspace/join invites (`/join?i=`,
`?invite`, `?tshare`) only preview by GET and redeem through an authenticated POST from the page;
`claim-admin` is POST-only with a session.
