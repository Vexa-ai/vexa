### Fixed

- Helm deployments can now pin every chart-owned and runtime-spawned image by immutable SHA-256
  digest, with render-time rejection of malformed digests and tag/digest ambiguity.
