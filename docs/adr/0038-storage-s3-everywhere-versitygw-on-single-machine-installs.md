# ADR 0038 — Storage: S3 everywhere; versitygw on single-machine installs; MinIO removed

**Status:** accepted · 2026-09-28 · settles [#1671](https://github.com/Vexa-ai/vexa/issues/1671) ·
retires the MinIO entries in `image-licenses.json` under
[ADR-0004](0004-open-source-dependency-and-license-policy.md)

## Context

Vexa stores recordings and bots' saved browser sessions through S3. Lite and Compose ran a MinIO
server beside the stack, and the Helm chart embedded one by default.

MinIO is AGPL-3.0, Category X under ADR-0004. It was allowed as a sidecar on one premise: operators
pull it directly from MinIO's registry, so Vexa never redistributes it. That premise has ended. The
upstream repository was archived in April 2026; `minio/minio` and `minio/mc` no longer exist on
Docker Hub, and quay.io refuses anonymous pulls. Fresh Lite, Compose and Helm installs fail at the
pull, and CI's runtime lanes have failed at the MinIO download since 2026-09-13.

## Decision

1. One storage interface and one implementation: S3. No application code or sealed contract
   changes; meeting-api and the bots keep their `MINIO_*` and `BOT_S3_*` variables.
2. Lite and Compose run **versitygw** (Apache-2.0) with its POSIX backend as the storage sidecar,
   pinned by digest. Objects are plain files in a local volume, with metadata in extended
   attributes.
3. The Helm chart, on Kubernetes and OpenShift alike, stores recordings in the operator's own S3,
   configured through `storage.s3` with credentials from a Secret. The built-in MinIO is removed and
   the chart ships no evaluation store. A stale `minio.enabled: true` fails with an upgrade message;
   the chart never falls back to pod-local storage.
4. Hosted production keeps its existing object storage.
5. Existing installs keep their data where it is. An upgrade never writes or deletes the old MinIO
   volume or PVC, and the start path carries no migration logic. Old recordings do not play back
   until the operator copies them: on Lite and Compose with the opt-in script
   (`make migrate-storage`), which verifies every object by SHA-256 and deletes nothing; on Helm
   with the steps in the Kubernetes guide. The upgrade guide says where recordings live now.
6. Every Compose start checks the store meeting-api will use: the bucket, then PUT, GET (bytes
   compared) and DELETE of a probe outside the recordings prefix. Any failure stops the stack before
   meeting-api starts. Compose and Lite print one WARNING line at every start while recordings go to
   a store other than the bundled one.
7. `make down` keeps data volumes. Deleting them is `make destroy DESTROY=yes`.
8. Nothing Vexa ships references a MinIO image. The `minio/minio` and `minio/mc` entries leave
   `image-licenses.json`, versitygw is recorded as Category A, and the image-licence gate also scans
   the image variables in the Lite Makefile.

## Alternatives considered

- **A one-time migration in the start path** (detect the old volume, then copy or block until
  copied): rejected. It leaves heuristics in the product that must be kept and later removed, and a
  migration that fails inside `make up` fails on the operator's machine with Vexa's code. An
  explicit, verifying script with documentation leaves the decision with the operator.
- **A CI-only stop-gap on the community MinIO build:** dropped. The swap removes MinIO from CI with
  everything else.
- **A Vexa disk adapter beside S3:** removes the sidecar, but adds a second storage implementation,
  a new bot-storage API and a sealed-contract change.
- **The community MinIO build in shipped files:** AGPL and a single maintainer; it repeats the
  dependency that just failed.
- **Bitnami's legacy MinIO images:** frozen since August 2025, without security updates.
- **SeaweedFS, Garage, RustFS, s3proxy:** run against the same S3 suite as versitygw. Only versitygw
  and s3proxy kept objects as plain files; SeaweedFS is heavier to run, Garage is AGPL, and RustFS
  is less mature. versitygw idles at about 15 MB of memory.
- **A bundled evaluation store in the Helm chart:** not shipped. Kubernetes operators run their own
  S3, and a bundled store would be a second storage path to support.

## Consequences

- Hosted and self-hosted run the same storage code path.
- One third-party server stays in single-machine installs. Its licence lets Vexa mirror or rebuild
  the image, and because Vexa only speaks S3, replacing it is a configuration change.
- Operators back up the storage volume with attribute-preserving copies (`rsync -aX`, `cp -a`); a
  plain copy keeps the bytes but loses content types. A key and a "folder" of the same name cannot
  both exist; Vexa's keys never do that.
- An upgraded install that skips the copy keeps recording; its old recordings answer HTTP 500 until
  copied. A stale `.env` that still points at a running old MinIO keeps writing there, and the
  start-up warning names it.
- Helm installs need an S3 bucket before install; there is no zero-configuration Kubernetes path.
