# @vexa/dash-chat — dashboard chat-panel view

_dashboard_new/ · view brick · `{ messages: ChatMessage[] }` → DOM (sender + text + time)._

A pure, presentational React component that renders in-meeting chat messages. **Props in, DOM out** —
no store, no fetch, no websocket. The vendored `dashboard/src/components/meetings/chat-panel.tsx`
pulled messages off `useMeetingsStore` and bootstrapped them over REST; this clean brick takes the
already-fetched array as a prop, so it is trivial to test (mount over golden props → assert the DOM)
and the data-wiring lives outside the view where it belongs.

## Surface

Front door: [`src/index.ts`](src/index.ts).

- `ChatPanel` — the component. `<ChatPanel messages={...} isActive? />`.
- types `ChatMessage`, `ChatPanelProps`.

## Props contract

```ts
interface ChatMessage {
  sender: string | null | undefined; // ws.v1 ChatMessageFrame.sender — shown as the bubble's sender ("Unknown" if absent)
  text: string;                      // ws.v1 ChatMessageFrame.text — the message body (required floor)
  timestamp?: number;                // Unix epoch ms — rendered as a short localized time when present
  is_from_bot?: boolean;             // bot messages align right and use the accent bubble
}

interface ChatPanelProps {
  messages: ChatMessage[]; // in display order (oldest → newest); injected, never fetched here
  isActive?: boolean;      // when true, shows a small "live" hint under the list (default false)
}
```

The message shape is anchored on the [`@vexa/dash-contracts`](../dash-contracts/) ws.v1
`ChatMessageFrame` floor (`sender` + `text`) and extended **additively** (the ws.v1 way) with the
optional display fields the bubble paints when present (`timestamp`, `is_from_bot`).

### Rendered DOM contract (what the L4 spec asserts)

- `section.dash-chat` — the panel root (`aria-label="Chat"`).
- `.dash-chat-bubble` — one per message (`data-from-bot="true|false"`).
  - `.dash-chat-sender` — the sender text.
  - `.dash-chat-time` — the formatted time (only when `timestamp` is present).
  - `.dash-chat-text` — the message body.
- `.dash-chat-empty` — shown instead of the list when `messages` is empty.

## Verify

`npm run build` — `tsc` clean (`tsconfig` adds the `DOM` lib + `react-jsx`).

L4 bulletproof test: a **real chromium** (Playwright) mounts the REAL `ChatPanel` over **2 golden
messages** and asserts the rendered DOM shows both messages with their senders + text + time. This is
the same proven pattern as the dashboard `e2e/` harness (esbuild bundles the component + a fixture page,
a tiny stdlib static server serves it, the Playwright spec loads it and asserts the DOM):

```
npm install            # react, react-dom, esbuild, @playwright/test
npm run install:browser  # one-time: playwright install chromium
npm test               # playwright test --config e2e/playwright.config.ts
```

A green here means "a human's browser renders the messages this component is handed" — not "a node
fake parsed JSON".
