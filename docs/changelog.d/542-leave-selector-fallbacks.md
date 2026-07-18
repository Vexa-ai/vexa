- **Google Meet leave: confirmation-dialog fallbacks work again, and the error spam is gone (#542).**
  The browser-context leave click fed Playwright-only `:has-text()` selectors to
  `document.querySelector`, so every dialog-confirmation fallback ("Leave meeting", "Just leave
  the meeting", dialog-scoped Leave/End) was silently dead, and each leave attempt logged a burst
  of `not a valid selector` errors that read like join failures (#432). Leave buttons are now
  matched in-page with plain CSS plus real text matching, and the selector-validity gate
  CSS-parses every array declared browser-context, so this class of dead selector fails CI
  loudly instead of shipping green.
