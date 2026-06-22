# meetings-list — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> The presentational meetings-list VIEW: a React component { meetings: MeetingResponse[], onOpen? } that renders one clickable row per meeting (platform icon, native id, status dot + label, duration). Props in, DOM out — no store, no fetch, no ws; data is injected and typed by @vexa/dash-contracts. The clean modular replacement for the vendored dashboard's app/meetings/page.tsx + components/meetings/meeting-list.tsx.

