- **Recordings: long recordings over ~1000 chunks are assembled in full (#769).** The chunk listing
  now paginates `list_objects_v2` to exhaustion (looping on `IsTruncated`/`NextContinuationToken`)
  instead of reading only the first 1000-key page, so a master built from a very long meeting no
  longer silently drops everything past the first page. A chunk-count mismatch is logged loudly.
