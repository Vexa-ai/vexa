- **The terminal's session secret is generated at install and required (#1868).** Compose's
  `make up` now mints `VEXA_TERMINAL_SESSION_SECRET` into `.env` like the other secrets. Without it
  admin-api, and the terminal in production, refuse to start and name the key, instead of signing
  everyone in with an ordinary key that no session-only decision accepts.
