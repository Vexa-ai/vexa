# Microsoft Teams Platform

Playwright-based bot integration for Microsoft Teams. Handles join with media device warm-up, mixed audio routing, and DOM-based speaker detection.

## Join Flow

1. `join.ts`: Calls `warmUpTeamsMediaDevices()` before join -- `getUserMedia({audio:true, video:true})` then stops tracks to prime browser permissions.
2. `waitForTeamsPreJoinReadiness()` ensures the pre-join screen is ready.
3. Name input, then click join.

## Audio Capture

Single mixed RTCPeerConnection -- all participants share one audio stream. Speaker identity is determined via DOM active speaker detection combined with voting/locking (same system as Google Meet, but applied to a mixed stream).

## Selectors

`selectors.ts` covers: continue button, join button, camera/video options, name input, audio radio buttons, speaker enable/disable.

## Platform Support

- `teams.live.com` links: verified working
- Enterprise links (`teams.microsoft.com/l/meetup-join/...`): untested -- org auth policies may block unauthenticated guests

## Known Issues

- 0% failure rate in current production
- Open issues: #171, #189, #190, #191
- Enterprise meeting links may be blocked by org policy
- Selectors may drift as Teams updates their web UI

## Development Notes

### Media Warm-up

The media warm-up step (`warmUpTeamsMediaDevices`) is critical -- without it, Teams may not properly initialize audio/video devices and the join can fail silently.

### Speaker Detection Challenges

Mixed audio makes speaker detection harder than Google Meet (which has per-speaker streams). The system relies on DOM active speaker indicators to attribute audio segments to speakers.
