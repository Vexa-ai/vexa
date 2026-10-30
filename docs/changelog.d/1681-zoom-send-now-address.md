- **Dashboard: "Send now" no longer offers a Zoom send it cannot address (#1681).** Zoom and
  Jitsi bots need the full invite link — the API builds a join URL only for Meet and Teams — but
  the meeting row sent one only when it had a stored link, and fell back to a constructed URL for
  Meet alone. A Zoom row with no stored link therefore produced a request refused with
  `unsupported platform 'zoom' without a meeting_url`. The row now says which link it needs, in
  the surface that offered the action, and stops before the request.
