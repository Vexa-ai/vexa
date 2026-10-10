- **Share a meeting, live and after, with its workspace (#1801).** The meeting header has one **Share**
  action. Invite people by email (each link works only for its address), optionally adding the
  meeting's workspace as *can view* or *can edit*; see who has access and remove anyone; create a
  sign-in link and turn it off; choose whether recipients may play and download the recording (off by
  default). New owner-only routes: `GET/PATCH /meetings/{id}/access`,
  `DELETE /meetings/{id}/share/{grant_id}`, `DELETE /meetings/{id}/viewers/{viewer_id}`. A removed
  reader loses access everywhere at once, including an open live view: the terminal stream ends with
  `access-revoked`, and `/ws` sends the new ws.v1 code `subscription_revoked`. Recipients no longer
  see bot controls, and a refused share link says why. See [Share a meeting](/how-to/share-a-meeting).
