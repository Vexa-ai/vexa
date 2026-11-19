- **Every requirement Vexa places on your platform, in one table.** The Kubernetes guide's
  Enterprise section now lists each demand on your database, network, nodes, identity provider,
  registries and cluster: who needs it, why, whether it is strictly needed, and how to avoid it. The
  no-egress sections now name the two calls that leave by default (the daily model-price read, which
  `VEXA_MODEL_PRICES_URL=off` stops, and the Codex CLI's own vendor traffic). A new principle, P24
  (least demand on the host, fewest primitives), and its gate `gate:host-demands` keep the table
  true: a new host-level demand in the chart, Compose, Lite or the services' SQL fails CI until it
  has a row. See [What Vexa requires of your platform](/deployment-kubernetes#what-vexa-requires-of-your-platform).
