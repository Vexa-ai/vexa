- **Release mechanics hardening (residual of #583)** — the page-side capture-brick build list now
  lives once, in `core/meetings/services/bot/page-bricks.list`; both image builds (the bot
  Dockerfile and `deploy/lite/Dockerfile.lite`) read it at build time and the new
  `gate:page-bricks` fails any reintroduced inline copy (the #576 rot class). Re-tagging a release
  is now scripted and guarded: `make release-retag VERSION=… EXPECT=<sha>` verifies the expected
  fix is on `origin/main` before moving the tag (dry-run by default, `CONFIRM=1` to act, then
  watches the tag build).
