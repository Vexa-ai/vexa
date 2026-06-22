# @vexa/dash-meetings-list — the presentational meetings-list VIEW

_dashboard_new/ · module · `MeetingResponse[]` props in → rendered meeting rows + click-out._

A single React component, `MeetingsList`. **Props in, DOM out.** No store, no fetch, no ws — the
meetings array is INJECTED and the click is reported through an injected callback. This is the CLEAN
modular replacement for the vendored dashboard's `app/meetings/page.tsx` +
`components/meetings/meeting-list.tsx`, which coupled the list to a Zustand store, a Next router, and a
fetch/infinite-scroll loop. None of that coupling lives here.

Each meeting renders as one accessible, clickable row: a platform icon, the human title
(`data.name || data.title || native id`) with the native id beneath it, a status dot + label, and the
duration. The whole row is the click target.

## Props contract

```ts
import type { MeetingResponse } from "@vexa/dash-contracts";

interface MeetingsListProps {
  /** Meetings to render, shaped per api.v1 MeetingResponse (GET /meetings items). REQUIRED. */
  meetings: MeetingResponse[];
  /** Called with the clicked meeting when a row is activated (click or Enter/Space). Optional. */
  onOpen?: (meeting: MeetingResponse) => void;
  /** Message shown when `meetings` is empty. Optional; defaults to "No meetings yet". */
  emptyMessage?: string;
}
```

- `meetings` is consumed verbatim — order is preserved, no client-side sort/filter/paginate. Filtering,
  searching, and loading-more belong to the owner that fetches; this brick only paints what it's given.
- `status` is mapped to a label + dot color through the same vocabulary as the vendored
  `MEETING_STATUS_CONFIG` (`active → Active`, `completed → Completed`, `failed → Failed`, …); an
  unknown status renders its raw string with a neutral dot.
- `platform` is mapped to a label (`google_meet → Google Meet`, `teams → Microsoft Teams`,
  `zoom → Zoom`, `browser_session → Browser`); the label is the icon's `aria-label` + `title`.
- duration = `round((end_time − start_time)/60s)` formatted compactly (`<1m` / `42m` / `1h 5m`); `—`
  when either endpoint is missing (e.g. a still-active meeting with no `end_time`).
- The title falls back to the native id, then `Meeting {id}`, when no `data.name`/`data.title` is set.

## DOM contract (for tests / consumers)

| selector | meaning |
| --- | --- |
| `[data-testid="meetings-list"]` | the root container (always present) |
| `[data-testid="meetings-empty"]` | the empty-state, shown instead of rows when `meetings` is empty |
| `[data-testid="meeting-row"]` | one per meeting; carries `data-meeting-id` + `data-status` |
| `[data-testid="meeting-platform"]` | the platform glyph (`aria-label` = platform label) |
| `[data-testid="meeting-native-id"]` | the native meeting id text |
| `[data-testid="meeting-status"]` | the status dot + label; `data-status` = the raw status |
| `[data-testid="meeting-duration"]` | the formatted duration |

Rows are `role="button"`, `tabIndex=0`, and activate on click **or** Enter/Space → `onOpen(meeting)`.

## Surface
`MeetingsList` (default + named) and the `MeetingsListProps` type. Front door: [`src/index.ts`](src/index.ts).

## Verify
- **Build:** `npm --prefix . run build` (`tsc`, `react-jsx`, DOM lib) — type-clean against
  `@vexa/dash-contracts`.
- **L4 (the gate):** `npm --prefix . test` — a REAL chromium (Playwright) loads
  [`e2e/fixtures/list-render.html`](e2e/fixtures/list-render.html), which esbuild-bundles the REAL
  `MeetingsList` ([`e2e/build-bundle.mjs`](e2e/build-bundle.mjs)) and mounts it over two golden
  meetings (one `active`, one `completed`). The spec
  ([`e2e/list-render.spec.ts`](e2e/list-render.spec.ts)) asserts both rows render with their statuses
  + durations + native ids, then clicks the active row and proves the injected `onOpen` fired with
  that meeting (read off `window.__opened`). Green-in-Playwright ⇒ green-for-the-human's-browser.
  First run only: `npm --prefix . run install:browser` to fetch chromium.

The same fixture graduates to the real app by mounting `MeetingsList` inside the dashboard page that
feeds it `meetings` from `@vexa/dash-api-client` and routes `onOpen` — the DOM assertions are unchanged.
