- **Lite: no bot or agent worker runs as root (#1784).** Every child the Lite runtime starts now runs
  as a non-root uid: an agent worker as its subject's own, a meeting bot as one of its own, each with a
  fresh private home directory. A child that cannot be isolated is refused rather than started as
  root. Valkey's data and the workload logs are readable by root only, Valkey's password is no longer
  on its command line, and the browser install is read-only. `make -C deploy/lite test` now also
  checks that no spawned process is root or can read another's environment.
