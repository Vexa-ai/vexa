- **The admin account designs the company's mail (#1783).** Every mail Vexa sends now goes through
  one path: sign-in links, workspace, meeting and chat shares, call invites, bot notices, minutes,
  reports, digests and replies. Each one is a *kind* with a starter template and a switch in
  `_global/POLICIES.md` (`mail.<kind>: on | off | admins`). Sign-in is always on. Sharing and call
  invites start on. Everything else starts off.
  - **New tools.** From the chat, the admin account edits a template (`mail_template_edit`),
    previews it (`mail_preview`), test-sends it to its own address (`mail_test_send`) and switches
    it on (`mail_kind_set`). A kind can be switched on only after its current version has been
    test-sent.
  - **Audit and caps.** Every attempt is audited: kind, template version, recipient domain and
    result. Daily caps come from `mail_caps`, under the operator's `VEXA_MAIL_MAX_PER_DAY`.
  - **Sender and delivery.** The sender is operator configuration (`VEXA_MAIL_SMTP_FROM`,
    `VEXA_MAIL_REPLY_TO`). One switch, `VEXA_MAIL_RELAY=capture|relay`, moves delivery from the
    capture double to real delivery. Test domains are never relayed.
  - **Shared mailboxes.** The mailbox reader is now read-only (`EXAMINE` and `BODY.PEEK[]`, so
    nothing is marked, moved or labelled). It acts only on meeting invitations and replies to
    Vexa's own mail, and drops everything else unstored. `sensor_test` shows what it would
    keep.
  - **Invite address.** Everyone can read Vexa's invite address (`mail_address`).
  - **Changes for existing deployments.**
    - Minutes, prepare, attendee-report and bot-notice mails are now off until the admin account
      switches them on.
    - Plain mail to the mailbox that is neither an invitation nor a reply to Vexa's own mail is
      dropped. Set `VEXA_MAIL_INBOUND_FILTER=all` to keep routing it.

  See [Design your company's mail](/flows/mail-design).
