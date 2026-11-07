- **Sign-in link requests are rate-limited (#1784).** One email address gets at most 5 links and one
  client address may ask for at most 20 per 15 minutes (`MAGIC_LINK_RATE_PER_ADDRESS`,
  `MAGIC_LINK_RATE_PER_IP`, `MAGIC_LINK_RATE_WINDOW_SECONDS`). The client address is the TCP peer
  unless the peer is loopback or named in `TERMINAL_TRUSTED_PROXIES` (addresses or CIDR ranges); then
  it is the address that proxy appended to `X-Forwarded-For`. Behind a reverse proxy, name it there so
  the limit counts clients rather than the proxy; Helm names the in-cluster ranges while the terminal's
  Service is `ClusterIP`. See [Configuration](/configuration).
