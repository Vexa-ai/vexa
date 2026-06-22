# dash-meeting-state — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The meeting FSM + live transcript assembly: a tiny framework-agnostic observable store (NOT Zustand) that composes the infra bricks via injected ports. createMeetingState({apiClient, wsClientFactory, meeting}) seeds segments from REST (getTranscripts), then connects live and merges the 0.10.6 two-map model (confirmed append-only by segment_id + pending-by-speaker replaced per tick). Closes the socket on completed/failed. Deterministic to test over the fakes.

