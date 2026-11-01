- **`destroy` no longer fails when something else removes the container first (#1384).** The
  docker backend treated the daemon's 409 "removal already in progress" as a failed reclaim, so
  a concurrent remover — an operator's `docker rm -f`, a GC, another deployment sharing the
  daemon — made teardown raise although the container was already going away. The reclaim is now
  judged by the container's confirmed absence (bounded wait), and still fails loudly if the
  container is genuinely stuck.
