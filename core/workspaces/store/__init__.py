"""workspaces.store — workspace git repositories held durably in S3-compatible object storage (WS-S3).

``objects`` (the bucket port and the self-test), ``lock`` (the per-workspace Redis lock),
``repostore`` (bundle chain + generation manifests, the cache), ``observer`` (agent-api's ref
observer and platform-file sync), ``client`` (a worker's hydrate and push-back; standard library
only), ``boot`` (agent-api's start-up), ``migrate`` (volume → bucket, with verification).
Import the explicit submodule; nothing is re-exported here.
"""
