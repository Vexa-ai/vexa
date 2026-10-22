- **On Kubernetes, the flows Pods can reach only what they use.** A new `<release>-flows-egress`
  policy allows DNS, the gateway's service door, the database, the credential broker (only with a
  relay password reference) and the mail hosts. List your mail servers in
  `networkPolicy.flows.mailEgress` as CIDRs with their ports. Without a list, only the mail ports to
  public addresses are open, which covers Gmail and Microsoft 365. An external database can be
  limited to `networkPolicy.flows.databaseEgress`. admin-api and crm-api now accept connections
  only from the components that call them. **A mail server on a private address needs a
  `mailEgress` entry after you upgrade.** See
  [Kubernetes: flows egress](/deployment-kubernetes#flows-egress).
