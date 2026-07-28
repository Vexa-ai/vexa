- **The terminal is white-labelable without forking it.** Six build args —
  `NEXT_PUBLIC_BRAND_NAME`, `_ACCENT`, `_ACCENT_LIGHT`, `_ON_ACCENT`, `_FONT` and `_LOGO_URL` —
  drive the product name in every user-visible string, the accent colour each theme derives from,
  the UI font, and the logo. All unset leaves the build byte-identical to before. The accent has a
  separate light-theme value because a colour picked for a dark UI is usually unreadable on white;
  the monospace face is deliberately not brandable, since transcripts and timestamps align on it.
  A ready-to-apply preset ships at `deploy/compose/.env.brand-dac`.
  `NEXT_PUBLIC_DEFAULT_BOT_NAME` also reaches the image for the first time: it was read in code but
  never passed through the Dockerfile, so the knob was dead in every container build.
