- **One meeting view: notes on top, the transcript under them.** A meeting opens the same way from
  its chat, the Calls list or a `/?meeting=…` link: the header (title, platform · date · people,
  *live · N min* while the bot is in), the meeting's **Notes**, and the **Transcript**. The separate
  Transcript tab and the "Brief"/"Minutes" page are gone, and the transcript is no longer shown twice.
  **Update notes** rewrites the notes from what was said since the last update (the same act as
  Extend on the meeting's page); **Create notes** makes them when a meeting has none. The transcript
  is its own panel: collapsed, a scrolling window under the notes that stays on the newest line;
  **Show all** opens it in a narrow, resizable sidebar beside the notes (a full-width sheet on a
  phone). Playback highlights the line being played and keeps it in view. Links with `view=transcript`, `minutes` or `brief`
  still work and open the meeting view. A notes update for a meeting the caller cannot read is now
  refused with `403` before a turn is queued. (#1836) See [Meetings](/minutes/meetings).
