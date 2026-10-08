- **Sign-in link requests are rate-limited (#1784).** One email address gets at most 5 links and one
  client address may ask for at most 20 per 15 minutes (`MAGIC_LINK_RATE_PER_ADDRESS`,
  `MAGIC_LINK_RATE_PER_IP`, `MAGIC_LINK_RATE_WINDOW_SECONDS`). Behind a proxy on a public address, name
  it in `TERMINAL_TRUSTED_PROXIES` so the limit counts clients rather than the proxy. See
  [Configuration](/configuration).
