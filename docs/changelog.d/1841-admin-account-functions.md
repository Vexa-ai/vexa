- **The admin account gets its four jobs: flows & mail, templates, friction, the company workspace.**
  Signed in as the admin account, an empty chat offers four areas, each opening a chat that does the
  job. **Flows & mail:** say *"only accept invites from bank.com"* or *"stop sending attendee
  follow-ups"* and it writes the rule in `_global/POLICIES.md` — new rules `mail_domains` (the inbound
  allow-list, ahead of `VEXA_FLOWS_MAIL_DOMAINS`) and `minutes_mail`, and `bot_joins_on_invite` is now
  enforced; the `mail_settings` tool shows what the mailbox accepts and sends, never a credential.
  **Templates:** `_global/kg/templates/<kind>.md` is the company's shape for new notes, editable by
  the admin account only and validated on save; new pages take its sections. **Friction:** the
  `friction_list` tool reads every report on the deployment, filtered and with secrets masked.
  **Workspace:** the admin account's first sign-in opens the company setup. Everyday accounts are
  refused all of it. See [Admin account](/minutes/admin-account).
