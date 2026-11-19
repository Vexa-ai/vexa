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
- **Helm: the control plane reaches only the endpoints you declare (upgrade action).** Control-plane
  egress was open to any address on any port. It now allows DNS, the chart's own Pods, the object
  store on its port, an external database and Redis on theirs, the Kubernetes API server (the
  runtime and the postgres-password hook only), and each entry of
  `networkPolicy.controlPlane.egress` (`{cidr, ports}`). **Before upgrading, list every external
  endpoint you configured**: model providers, speech-to-text, SMTP, git remotes, the OIDC issuer,
  Microsoft Graph, Google and Telegram, webhook targets, and `openrouter.ai` for the daily model
  prices (or `VEXA_MODEL_PRICES_URL=off`). Anything unlisted is blocked once the upgrade lands.
  `networkPolicy.controlPlane.anyAddress: true` keeps the old rule while you collect them. See
  [Control-plane egress](/deployment-kubernetes#control-plane-egress).
- **Helm: flows uses the main database, and no role needs CREATEDB (upgrade action if flows is on).**
  `flows.databaseName` now defaults to empty: flows keeps its tables in `database.name`, as Compose
  and Lite do. Vexa no longer creates a database; the flows `ensure-db` init container only checks
  and stops with the step to take. **If you ran flows before, its data is in the database `flows`:
  set `flows.databaseName: flows` before upgrading** to keep using it. Without it, the flows Pods
  stop at `ensure-db` with that instruction rather than start empty. A separate database must be
  created by your DBA. See [Flows database](/deployment-kubernetes#flows-database).
