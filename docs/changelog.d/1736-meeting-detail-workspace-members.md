- **Workspace members can open the meetings their list shows (#1736).** `GET /meetings/{id}` now
  authorizes by the same rule as `GET /meetings` — owner, transcript-share recipient, or member of
  the meeting's bound workspace — so a member no longer gets 404 on a meeting listed for them.
  Anyone outside that set still gets 404, and members still receive the non-owner view of `data`.
