# @vexa/dash-status-history — presentational status-timeline VIEW

_dashboard_new/ · module · `StatusTransition[]` → a status-history `<ol>`._

A React component that renders a meeting's status history as a vertical timeline — one row per
transition, oldest → newest, each row showing the destination status (`to`) and its time. **Props in,
DOM out.** No store, no fetch, no ws, no Zustand, no shadcn/lucide — the data is INJECTED by the
caller (the dashboard wires it from a meeting's `status_transition` field). Typed by
[`@vexa/dash-contracts`](../dash-contracts/): each transition's `to`/`from` is the sealed
`MeetingStatus` union (kept open as `| string` so a new backend status renders instead of crashing).

This is the clean modular rebuild of the vendored
`clients/dashboard/src/components/meetings/status-history.tsx` — same behavior (sort oldest → newest,
one row per transition, status label + time + optional reason/source, newest highlighted), without the
app coupling (no `@/lib/utils`, no Collapsible, no icon library, no docs link).

## Props contract

```ts
interface StatusTransition {
  from?: MeetingStatus | string;        // optional prior status
  to: MeetingStatus | string;           // required destination status (the row's headline)
  timestamp: string;                    // ISO/UTC; a bare YYYY-MM-DDTHH:MM:SS is read as UTC
  reason?: string;                      // optional human reason
  completion_reason?: string;           // optional; used as the reason if `reason` is absent
  source?: string;                      // optional origin (e.g. "user", "bot_callback")
}

interface StatusHistoryProps {
  transitions?: StatusTransition[];     // undefined / [] → renders nothing (returns null)
  className?: string;                   // appended to the root <ol> class
}
```

```tsx
import { StatusHistory } from "@vexa/dash-status-history";

<StatusHistory transitions={meeting.status_transition} />;
```

### Rendered DOM

```
<ol class="status-history" data-count="N">
  <li class="status-history__row" data-status="<to>" data-index="i" [data-current]>
    <span class="status-history__dot" aria-hidden="true"></span>
    <span class="status-history__status">Joining</span>
    <time class="status-history__time" datetime="<iso>">10:00:00</time>
    [<span class="status-history__from">from Requested</span>]
    [<span class="status-history__reason">meeting ended</span>]
    [<span class="status-history__source">bot callback</span>]
  </li> …
</ol>
```

- Transitions are sorted oldest → newest by `timestamp` (stable for equal/unparseable timestamps).
- The newest row carries `data-current` (the "where we are now" anchor for styling).
- Class names are BEM-style hooks only — the brick ships no CSS; the host app styles them. Unknown
  status values pass through verbatim as the label (additive-safe).

## Surface

`StatusHistory` (named + default) · types `StatusHistoryProps`, `StatusTransition`.
Front door: [`src/index.ts`](src/index.ts).

## Verify

`npm run build` — `tsc` clean (`tsconfig` adds `DOM` + `react-jsx`; the `@vexa/dash-contracts` import
is type-only).

`npm test` — the **L4 bulletproof gate**: a real chromium (Playwright) loads a static fixture
([`e2e/fixtures/render.html`](e2e/fixtures/render.html)) that mounts the REAL component via react-dom
over golden transitions (`joining → active → completed`, supplied out of order), and
[`e2e/status-history.spec.ts`](e2e/status-history.spec.ts) asserts the REAL DOM shows all three
statuses **in order**, the newest marked `data-current`, and the injected completion reason. esbuild
bundles the component from source on every run (`globalSetup`), so the page exercises the current
brick. Green-in-Playwright ⇒ green-for-the-human's-browser. If chromium isn't present:
`npm run install:browser`.
