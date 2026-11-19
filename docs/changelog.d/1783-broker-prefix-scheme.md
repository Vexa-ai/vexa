- **Custom services can name their authentication word, and unfinished setups stop piling up
  (#1783).** A custom connection's setup takes `scheme: prefix` with a `prefix` such as `Bot` or
  `Token`, sending `Authorization: <prefix> <key>`; Discord bots need `prefix: Bot`, which no earlier
  scheme could produce. `bearer`, `raw` and `telegram` are unchanged. Asking again for a service whose
  setup is unfinished reuses it, a new setup for the same service replaces the unfinished ones, and
  an unfinished setup left for 24 hours is no longer listed in Connections; saved connections are
  never touched. A connection set up with the wrong scheme is fixed by asking the agent to set it up
  again and saving the key again. See [Connections](/connections#custom-services).
