- **`make probe` — the standing full-journey smoke probe, per surface (#690).** One command per
  install surface (`make probe`, `SURFACE=compose|lite|helm`) drives the whole journey through the
  gateway front door — spawn → schedule → boot → join → transcribe → live-view → stop — then sweeps
  every component's logs once. Each stage prints Expected / Actual / Verdict and a red stage names
  where the journey broke, so an operator (or an agent mid-debug) gets a truthful install verdict
  in minutes with zero humans. See [Kubernetes deployment](/deployment-kubernetes) and the
  `deploy/*/probe.sh` wrappers.
