- **Signed-out detection on Google Meet no longer depends on English UI text (#757).** The
  authenticated lobby's join CTA and the signed-out guest-lobby probe are now located by
  structural (jsname/attribute) selectors first, with English text as last-resort fallbacks —
  so a non-English lobby still fails closed with the actionable `auth_session_missing` error
  instead of a misleading "no join button found" timeout. Both selector arrays are covered by
  the selector-validity gate.
