# Zoom Platform

Zoom SDK integration via the shared `meetingFlow` strategy pattern. Self-hosted only -- not available on the hosted service.

## Architecture

Uses `PlatformStrategies` with 6 standalone strategies:

| Strategy | File | Purpose |
|----------|------|---------|
| Join | `strategies/join.ts` | SDK initialization and meeting join |
| Admission | `strategies/admission.ts` | Waiting room / host admission handling |
| Prepare | `strategies/prepare.ts` | Pre-meeting setup |
| Recording | `strategies/recording.ts` | Audio/video recording control |
| Removal | `strategies/removal.ts` | Ejection/removal handling |
| Leave | `strategies/leave.ts` | Graceful meeting exit |

## SDK Requirements

- Proprietary Zoom Meeting SDK binaries required (not included in repo)
- Must be placed at `native/zoom_meeting_sdk/`
- Requires your own Zoom Marketplace app credentials
- `sdk-manager.ts` manages SDK lifecycle with OBF token auth

## Web App Mode

A Playwright-based alternative (no SDK required) is in development under `strategies/src/`. Not yet released.

## Known Issues

- SDK binaries must be manually provided -- build fails without them
- OBF token auth has limitations
- Web app mode is incomplete
