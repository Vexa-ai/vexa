- **Teams bot leave — dead browser-context fallbacks revived (#759).** The Microsoft Teams
  leave path ran its fallback list through the browser's `document.querySelector`, but 10 of the
  25 entries were Playwright-only `:has-text()` locators — invalid CSS that threw on every call,
  so every text-labelled button (all three leave-confirmation dialog buttons included) was
  silently un-clickable and each leave attempt logged 10 "not a valid selector" errors. The array
  is now a `{ css | text }` matcher list driven by the same shared in-page clicker as Google Meet
  (#542), and it joins the selector-validity gate's browser-context lane so the class fails loudly
  for Teams too.
