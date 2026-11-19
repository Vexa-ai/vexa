- **Helm: a namespace default-deny (#1784).** With `networkPolicy.enabled` (the default), every Pod
  in the release's namespace now takes no connection and makes none unless a policy allows it. The
  chart allows its own traffic: the gateway, terminal and dashboard take connections from anywhere;
  the database only from the services holding its credentials; Redis from the chart and the Pods the
  runtime spawns; the control plane may reach DNS, the chart's Pods and any address outside the cloud
  metadata range. **Install Vexa in a namespace of its own**, or give other Pods there their own
  policies; add a metrics scraper with `networkPolicy.controlPlane.extraIngress`; empty
  `networkPolicy.controlPlane.egressExcept` if a control-plane Pod authenticates through the metadata
  server. `networkPolicy.defaultDeny.enabled=false` turns the default off.
