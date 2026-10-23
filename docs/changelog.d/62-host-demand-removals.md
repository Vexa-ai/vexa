- **Every chart Pod runs under the RuntimeDefault seccomp profile.** `global.podSecurityContext`
  now defaults to `seccompProfile: RuntimeDefault`, and the in-chart Postgres, Redis and PgBouncer
  take that profile (and no uid). A Pod Security `restricted` namespace admits every non-root
  Vexa Pod and every meeting bot; the Pods that run as root are listed in the requirements table.
  The datastores restart once on upgrade, because their Pod template changes. See
  [What Vexa requires of your platform](/deployment-kubernetes#what-vexa-requires-of-your-platform).
- **DNS egress goes to the cluster DNS Pods only.** Every NetworkPolicy the chart renders (control
  plane, flows, meeting bots, agent workers) now sends port 53 to kube-dns/CoreDNS in `kube-system`
  instead of to any address. `values-openshift.yaml` selects `openshift-dns` on 53 and 5353. A
  cluster whose DNS runs elsewhere (NodeLocal DNSCache, a custom label) sets `networkPolicy.dns.to`
  and `networkPolicy.dns.ports`; an empty `to` restores the old rule.
- **Compose: the runtime no longer runs as root.** It runs as uid 10001 and reaches the Docker
  socket through the socket's group. `mint-dev-env.sh` (which `make up` runs) now writes that group
  into `DOCKER_GID` on Linux, replacing the old default `0`; a value you set is kept. If the runtime
  logs `permission denied` on the socket after upgrading, set `DOCKER_GID` to
  `stat -c %g /var/run/docker.sock`.
