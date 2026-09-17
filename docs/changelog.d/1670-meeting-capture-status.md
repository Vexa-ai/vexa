- **A recorded meeting no longer reads "nothing captured" (#1670).** The meetings list omits the
  heavy `data.recordings` key, which was the only capture evidence a list row carried — so clients
  deriving capture from it marked every past meeting as unrecorded, including ones with a recording
  and a transcript. List and detail rows now carry a top-level `has_capture` boolean, derived from a
  stored recording or a non-zero `segments_captured`. See
  [Meetings API](/api/meetings#list-rows-are-slim-detail-is-full).
