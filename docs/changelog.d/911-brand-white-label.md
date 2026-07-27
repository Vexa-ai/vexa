- **The terminal is white-labelable without forking it.** Four build args —
  `NEXT_PUBLIC_BRAND_NAME`, `NEXT_PUBLIC_BRAND_ACCENT`, `NEXT_PUBLIC_BRAND_ON_ACCENT` and
  `NEXT_PUBLIC_BRAND_LOGO_URL` — now drive the product name in every user-visible string, the accent
  colour both themes derive from, and the logo. All unset leaves the build byte-identical to before.
  `NEXT_PUBLIC_DEFAULT_BOT_NAME` also reaches the image for the first time: it was read in code but
  never passed through the Dockerfile, so the knob was dead in every container build.
