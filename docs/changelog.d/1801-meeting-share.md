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
- **Readers who joined before the roster are named (#1801).** On the owner's first access view,
  meeting-api backfills each such reader's address from identity (new internal-only
  `GET /internal/users/{id}/email`) and links the invite they used, so it no longer shows as pending
  and removing them withdraws it.
- **Sharing hardened after review (#1801).** An open `/ws` subscription is re-checked by the meeting row
  it streams; invite emails go to one plain address each, at most 30 per owner per hour, with the
  title as one bounded line and no token in the email fact (flows mints the link at send time);
  turning a link off removes exactly the readers it admitted; a calendar series' new occurrence keeps
  its workspace only while its owner can still edit it; the terminal stream re-checks every 10 s and
  notices a deleted transcript; a worker's write set is bounded by its ceiling; the identity email
  lookup has no dev-mode bypass; readers without recording permission see no recording metadata.
