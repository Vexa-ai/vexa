- **One outbound URL guard (#1784).** Webhook deliveries, calendar feeds, the agent's web and image
  fetches, custom-service connections, repository attaches and the Settings → Models endpoint gate
  now share one check: an address is judged by where a connection to it lands, whichever notation
  it is written in, and the connection is made to the address that was checked. A calendar feed
  naming an internal destination is refused with `422` when it is saved.
