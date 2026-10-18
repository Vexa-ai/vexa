# workspaces · store — workspace repositories in S3-compatible object storage (WS-S3)

The s3 workspace backend (`VEXA_WORKSPACE_BACKEND=s3`; Helm `agentApi.workspaces.backend: s3`). The
bucket is the single durable truth for every workspace repository and platform file; agent-api's
`VEXA_WORKSPACES_DIR` and each worker's `/workspaces` are disposable caches. The default backend,
`volume`, runs none of this.

**The guarantee:** a write is acknowledged only after its commit and ref update are durable in the
bucket. A pod can die at any instant without losing an acknowledged write; a cold pod rebuilds its
cache from the bucket. Bucket unreachable → writes fail loudly (503), reads are served from the cache.

| Module | What it is | Imports |
|---|---|---|
| `objects.py` | The bucket port (`get/put/delete/list`), the boto3 adapter for any S3-compatible endpoint (path-style, private CA, optional region, static key), the in-memory double, and the boot self-test | boto3 (lazily) |
| `lock.py` | The per-workspace Redis write lock and its monotonically increasing fencing token | redis client passed in |
| `repostore.py` | Bundle chain + one manifest per (generation, fencing token); publish with fence check and generation election (no conditional PUT), hydrate (cold/warm/stub), rollback, worker push/fetch, platform files | `gitexec`, `workspace_paths` |
| `observer.py` | agent-api's observer on `gitexec` (access hook; lock → fresh → git → publish) and `workspace_paths` (access hook; platform writes published before they return), and `CacheSync` for whole-tree moves | — |
| `client.py` | A worker's hydrate of its own dispatch's mounts and its push-back observer, over agent-api's store routes. Standard library only | `gitexec`, `workspace_paths` |
| `cache.py` | agent-api's lazy cache: stubs hydrated on first use (`workspace_paths` / `gitexec` access hooks), LRU eviction under `VEXA_WORKSPACE_CACHE_MAX_BYTES` | `gitexec` |
| `boot.py` | agent-api's start-up from env: self-test, hydrate, install the observer | — |
| `migrate.py` | `python -m workspaces.store.migrate`: volume → bucket, verified from a fresh cache | — |

Design and trade-offs: `~/dev/biz/drafts/briefs-0133/ws-s3-design.md`. Product explainer:
[`docs/docs/core/workspaces.mdx`](../../../docs/docs/core/workspaces.mdx) "Where repositories live".
Tests: `core/agent/tests/test_workspace_store.py`, `test_workspace_store_service.py`; any suite can be
run with the s3 write path switched on (`VEXA_TEST_WORKSPACE_BACKEND=s3`, `core/agent/tests/conftest.py`).
