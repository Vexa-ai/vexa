- **Teams: a benign alert no longer evicts a just-joined bot (#600).** The Teams removal monitor
  matched a generic `[role="alert"]` (plus `[role="alertdialog"]`, `.error-message`,
  `.connection-error`, `.meeting-error`), so a transient toast or the post-join AV-confirmation modal
  could trip a false removal ~1.5s after admission and self-leave with `completed(evicted)` and no
  transcript. The removal signal is now the removal/"meeting ended" text only.
