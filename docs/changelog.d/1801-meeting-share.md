- **Share a meeting, live and after, with its workspace (#1801).** The meeting header has one **Share**
  action. Invite people by email (each link works only for its address), optionally adding the
  meeting's workspace as *can view* or *can edit*; see who has access and remove anyone; create a
  sign-in link and turn it off; choose whether recipients may play and download the recording (off by
  default). New owner-only routes: `GET/PATCH /meetings/{id}/access`,
  `DELETE /meetings/{id}/share/{grant_id}`, `DELETE /meetings/{id}/viewers/{viewer_id}`. A removed
  reader loses access everywhere at once, including an open live view: the terminal stream ends with
  `access-revoked`, and `/ws` sends the new ws.v1 code `subscription_revoked`. Recipients no longer
  see bot controls, and a refused share link says why. See [Share a meeting](/how-to/share-a-meeting).
- **Invited people are emailed their link (#1801).** `POST /meetings/{id}/share` takes `notify: true`
  for a restricted grant: meeting-api hands a `meeting.shared` fact to flows, whose new `meeting_share`
  flow mails each address the link (template `behavior/mail/meeting-share.md`; the link is composed by
  flows from `VEXA_UI_URL`). The dialog says who was emailed and keeps Copy link.
- **Putting a meeting in a workspace takes edit access there (#1801).** Identity now signs
  `writable_workspaces` (gateway-identity.v1 and identity.v1, additive), and meeting-api refuses with
  `403` a bind into a workspace the caller can only view or does not belong to — the bind route,
  planned meetings and `POST /bots` alike.
