- **Meeting recordings play in the browser.** Past meetings with a recording now show a player on
  the meeting tab — video when the deployment captured it, audio otherwise, with a download link
  either way. Recordings were always captured and stored; nothing could open one, because the
  terminal had no player and `recordings` was not on its proxy's meetings-domain list. Seeking works
  (range requests are forwarded verbatim), so scrubbing does not refetch the whole file.
- **A recording no longer costs double once you play it.** When a recording is complete and its
  assembled master is verified on storage, the 15-second chunks the master was built from are
  deleted — they exist so a bot killed mid-meeting leaves every finished part durable, and a
  verified master holds the same bytes. Steady-state storage is halved for every recording anyone
  opens, and a one-hour recording is one object instead of ~240. A recording still in progress is
  never pruned, and `RECORDING_PRUNE_CHUNKS=false` keeps both copies.
