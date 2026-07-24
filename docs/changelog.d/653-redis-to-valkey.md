- **Every deploy surface now runs Valkey 8.1.9 instead of Redis (#653).** Vexa Lite, Docker
  Compose, and Helm all use [Valkey](https://valkey.io) — the Linux Foundation's BSD-3 fork of
  Redis 7.2.4 — for the bus, scheduler, and per-dispatch streams. This gives every surface (Lite
  included) `XAUTOCLAIM` orphan-reclaim parity, and moves off source-available Redis ≥7.4
  (RSALv2/SSPLv1). The store is wire-compatible (RESP): `REDIS_URL` and every `redis.*` config key
  are unchanged, so existing overrides keep working. See [Deployment](/deployment).
- **Two new license/parity gates close the class the audit exposed (#653).** `gate:image-licenses`
  audits container-image pins and image-baked binaries (which `gate:licenses` never saw), and
  `gate:runtime-parity` asserts every surface's engine version supports the RESP commands the code
  actually calls — the rung that catches a backing store shipped without a capability the code
  assumes.
