# flowGraph — a flow, drawn read-only

`model.ts` turns a flow definition (and, when the server sent them, its last run, today's count and the
background-policy gate) into nodes — trigger → steps → policy gate → outputs — plus the cron reader the
routine strip's countdown uses (`nextRun`, `cronText`, `scheduleLine`). `FlowGraph.tsx` draws them as
boxes and arrows (left to right; top to bottom on a phone), muted text only, and peeks a node's
configuration on click. No editor. Used by the routine strip (`minutes/RoutineBar.tsx`) and the
blocks.v1 `flow` block.
