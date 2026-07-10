# deploy/lite/tests — smoke tests against the PUBLISHED lite image

- `concurrent-bots.sh` — the release smoke test and the **sole issuer** of the
  `release/vm-validated` commit status: ≥2 concurrent bots must reach `joining`
  on per-bot profile dirs with zero Chromium SingletonLock signatures (the #478
  failure class fires at browser launch, so no meeting admission is needed).
  Run on a clean host after `IMAGE_TAG=vX.Y.Z make lite`; post the attestation
  with `POST_STATUS=1 GIT_SHA=<released sha>`. Contract: [../release/README.md](../../release/README.md).
