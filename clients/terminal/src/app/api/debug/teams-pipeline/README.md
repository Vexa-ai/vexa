# Teams pipeline witness bridge

Development-only, same-origin bridge for `/debug/teams-pipeline`. It is disabled unless both
`VEXA_TEAMS_PIPELINE_BACKEND` and `VEXA_TEAMS_PIPELINE_REFERENCE` are explicitly set and
`NODE_ENV=development`. It proxies the local wall-clock candidate event stream and reads only the
one explicitly configured reference artifact. It is not a production API.

The same bridge proxies the replay backend's byte-range `/audio.wav` response as `/audio`, so the
transcript and its exact cut share one lifecycle and no second fixture server is required.
