# chat — source

The brick's implementation. The single public surface is `index.ts` (the front door); everything else
here is internal to the brick and reached only through it.

> Presentational dashboard chat view: a React component {messages: ChatMessage[]} that renders in-meeting chat messages (sender + text + time). Props in, DOM out — no store, no fetch, no ws; data is injected and typed by @vexa/dash-contracts. The clean modular replacement for the vendored chat-panel.tsx (which coupled to the meetings store + REST bootstrap).

