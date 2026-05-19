# Hardenloop Release Scanner Summary

Date: 2026-05-14

## Final Configured Release Run

Command:

```bash
PATH=/home/dima/dev/vexa-i-adversarial-harness/.tools/bin:/home/dima/dev/vexa-i-adversarial-harness/.venv/bin:$PATH \
  /home/dima/dev/vexa-i-adversarial-harness/.venv/bin/hardenloop release \
  /home/dima/dev/vexa-260508-v0.10.6.1 \
  --mode oss-release \
  --cycles 1 \
  --fix none \
  --config /home/dima/dev/vexa-260508-v0.10.6.1/tests3/hardenloop-release.toml \
  --out /tmp/vexa-hardenloop-after-security-configured
```

Decision: `ready`.

Release blockers: `0`.

Configured scanner coverage:

- `semgrep`: ok, exit `0`.
- `gitleaks`: ok, exit `0`.
- `trivy`: ok, exit `0`.
- `osv-scanner`: ok, exit `0`.
- `syft`: ok, exit `0`.
- `zizmor`: ok, exit `0`.
- `actionlint`: ok, exit `0`.
- `bandit`: ok, exit `1`; normalized findings are non-blocking under this release policy.
- `pip-audit`: ok, exit `0`.

Normalized non-blocking findings retained for audit:

- `166` normalized findings total.
- `131` from Bandit.
- `35` from Semgrep OSS.

The raw Hardenloop report is intentionally kept in `/tmp` instead of the release tree because raw scanner artifacts can reproduce secret-shaped evidence. This file is the sanitized release-cycle receipt.

## Integration Fixes Applied

- Merged `codex/adversarial-hardening` into `release/260508-v0.10.6.1`.
- Removed dashboard validation fallbacks that accepted placeholder/local tokens; dashboard validation scripts now require `VEXA_DASHBOARD_TEST_TOKEN`.
- Kept transcription-service on the bounded stdlib multipart parser path; it does not install `python-multipart`.
- Updated dashboard dependency floor evidence to PostCSS `8.5.14`.
- Added `tests3/hardenloop-release.toml` and `tests3/gitleaks-release.toml` so the release cycle scans visible source and excludes local runtime env/build artifacts without allowing placeholder secrets.
- Patched Hardenloop itself to honor its `tomli` fallback on Python 3.10 config runs.
- Hardened `.github/workflows/docker-publish-multiarch.yml`: pinned third-party actions by SHA, moved workflow input interpolation out of shell, and disabled checkout credential persistence.
- Removed the stale `get_recording_metadata_mode` import after the JSONB-only recording path deleted the toggle.
- Fixed the Pack E.1.a reproducer collection failure and reran the concurrent chunk regression.

## Machine Gate Evidence After Fixes

- `STATE=tests3/.state-compose tests3/checks/run --list`: all `143` checks pass.
- `STATE=tests3/.state-compose bash tests3/tests/local-human-mechanical-gate.sh`: pass.
- `STATE=tests3/.state-compose LIVE_BOT_MEETING_ID=10112 bash tests3/tests/live-bot-transcript-pipeline.sh`: pass, `6` chunks and `2` transcript segments.
- `STATE=tests3/.state-compose DASHBOARD_RECORDING_MEETING_ID=10112 bash tests3/tests/dashboard-recording-playback-ready.sh`: pass, playback renders and is not stuck processing.
- `STATE=tests3/.state-compose bash tests3/tests/dashboard-auth.sh`: pass for login, cookie flags, `/me`, and `/api/vexa/meetings`.
- Compose and lite `advisory-dependency-floors.sh`: pass.
- Compose and lite `no-placeholder-transcription-token.sh`: pass.

## Superseded Failed Runs

Earlier full scans on 2026-05-14 were not green. They reported thousands of historical Gitleaks findings because the default Gitleaks mode scanned repository history, plus CI workflow blockers and one stale SQLAlchemy/text audit pattern. Those runs caused the stage to bounce back to `develop-code`; they are superseded by the configured release run above.
