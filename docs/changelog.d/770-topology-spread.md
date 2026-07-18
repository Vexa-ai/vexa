- **Helm: spread replicas across nodes with `topologySpreadConstraints` (#770).** `replicaCount > 1`
  no longer means "all on one node" — set `global.topologySpreadConstraints` (applied to every
  component) or a per-component `<component>.topologySpreadConstraints` override, and the chart
  injects that component's own pod selector when you omit `labelSelector`, so a single block spreads
  each component's own replicas. Empty by default (single-node / k3s installs are unaffected). See
  the chart README's "Spreading replicas across nodes".
