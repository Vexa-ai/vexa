# `/api/recording-media` — recording byte-stream proxy

Same-origin streaming proxy for a finished meeting's recording master (the `<video>`/`<audio>` src),
used by [`canvas/RecordingPlayer`](../../../canvas/RecordingPlayer.tsx).

Why its own route (not the generic `[...path]` proxy): the catch-all buffers the body as text and
forces `application/json` — fine for JSON, fatal for video. This route forwards the HTTP `Range`
header and passes the upstream **status + headers + body stream** through verbatim, so the media
element can seek without downloading the whole master.

- **`GET /api/recording-media?rec=<recordingId>&mf=<mediaFileId>&type=<combined|video|audio>`**
  → forwards to the gateway's `/recordings/{rec}/media/{mf}/raw?type=...`, injecting `X-API-Key`
  server-side (the browser never sees the key or the backend host).

`type=combined` is the muxed audio+video master; `video` the silent video-only master; `audio` the
audio master. The player asks the backend `/recordings/{id}/master?type=...` first (which finalizes
the master and returns the `media_file_id`), then points the element here.
