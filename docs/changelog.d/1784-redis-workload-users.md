- **Bots and agent workers connect to Redis as users of their own (#1784).** Redis now requires a
  password on every shape (`REDIS_PASSWORD`: compose mints it with `make up` and refuses to start
  without one, Helm generates it and keeps it across upgrades, Lite mints one per boot), and only the
  services hold it. Each agent worker connects as a Redis user limited to its own unit's input stream,
  output stream and read cursor, and each meeting bot as one limited to appending to the transcript
  stream and its own meeting's two channels; agent-api and meeting-api define them per spawn, remove
  them when the work ends, and define them again after a Redis restart. A Redis that cannot define
  users can opt out with `REDIS_WORKLOAD_ACL=shared`, which is safe only where every person on the
  instance trusts every other. With your own Redis, the services' user needs `ACL SETUSER`,
  `ACL DELUSER` and `ACL GETUSER`. See [Configuration](/configuration#secrets--identity).
