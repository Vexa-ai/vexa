# app/meetings/[meetingId]

One page: the workbench, with the meeting id carried in the path. `Workbench.resolveFirstView` reads it
(via `meetingIdFromPath`) at first render and opens that meeting's tab; an id that resolves to nothing
renders the meeting tab's not-found state.
