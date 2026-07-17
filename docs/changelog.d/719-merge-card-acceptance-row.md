- **Merge card: acceptance row — `Closes` can no longer silently drop delivery legs (#712).** The
  merge card grows a third row: every issue a PR would auto-close must have its whole Acceptance
  section delivered (checkbox and legacy-bullet shapes both parsed); undelivered legs red-card the
  PR until they ship, carry delivered evidence on the issue, or the link is re-filed as
  `Part of #N`. A dropped acceptance leg is now a decision on record, never a GitHub-keyword
  side-effect. See [Delivery](/governance/delivery#integration-—-the-merge-bar).
