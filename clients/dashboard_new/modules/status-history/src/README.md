# status-history — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Presentational status-timeline VIEW: a React component { transitions: StatusTransition[] } that renders a meeting's status history oldest → newest, one row per transition (destination status + time, plus optional from/reason/source). Props in, DOM out — no store, no fetch, no ws; data is injected. Typed by @vexa/dash-contracts (each transition's to/from is a MeetingStatus).

