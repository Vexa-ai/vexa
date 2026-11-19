# join/src/zoom/__fixtures__ — captured Zoom web-client pages

Pages captured from `app.zoom.us/wc/<id>/join?wpk=…` on 2026-10-21 and trimmed to what the join
reads; meeting ids, tokens and asset version paths replaced. `../prejoin.test.ts` plays them in the
order Zoom serves them.

- `zoom-joining-shell.html` — the web client's "Joining Meeting..." loading shell.
- `zoom-hold-host-meeting-in-progress.html` — the self-refreshing hold page shown while the host's
  account is in another meeting.
- `zoom-prejoin-react.html` — the React pre-join with the name field and Join button.
