# Teams candidate live witness

Development-only page that renders the wall-clock m26123 replay through the exact
`LiveTranscriptEngine` used by the terminal meeting canvas. Inputs and transcript events come from
the actual `TeamsCsrcGmeetPipeline` replay service through the same-origin debug bridge. Stable
2-second pipeline cadence and transcript-edge lag against synchronized audio are shown separately.
SSE reconnects receive the pipeline's current segment snapshot, so a Fast Refresh preserves rows,
earned speaker names, and contested-word annotations instead of waiting for future repaints.
