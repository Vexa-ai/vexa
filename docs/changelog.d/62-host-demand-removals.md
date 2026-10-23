- **Every chart Pod runs under the RuntimeDefault seccomp profile.** `global.podSecurityContext`
  now defaults to `seccompProfile: RuntimeDefault`, and the in-chart Postgres, Redis and PgBouncer
  take that profile (and no uid). A Pod Security `restricted` namespace admits every non-root
  Vexa Pod and every meeting bot; the Pods that run as root are listed in the requirements table.
  The datastores restart once on upgrade, because their Pod template changes. See
  [What Vexa requires of your platform](/deployment-kubernetes#what-vexa-requires-of-your-platform).
