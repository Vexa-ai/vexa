---
enabled: true
view: views/meeting.tsx
---
<!-- Steering for the Meeting Canvas author. This file is the UI contract for the generated
     meeting view. Edit views/meeting.tsx, not the terminal runtime. -->

You author `views/meeting.tsx`: a single default-exported React component for the terminal Meeting Canvas.
The terminal validates, transpiles, trial-renders, and only then promotes it. Stay on the rails.

## Data Hooks

Use these hooks only. They are scoped to the current meeting, return safe empty defaults, and never expose raw
fetch, WebSocket, DOM, or event-subscription primitives.

```ts
const { segments, liveCaption } = useTranscript({ by?: "time" | "speaker", window?: number })
// segments: { speaker?: string; text: string; ts?: number | string }[]

const speakers = useSpeakers()
// { name: string; segments: number; talkMs: number; talkPct: number }[]

const entities = useEntities({ kind?: "person" | "company" | "product" | "number" })
// { kind, title, subtitle?, body?, value? }[]
```

Do not use `useMeeting` in generated views. The harness owns the meeting snapshot and exposes only the shaped hooks above.

## Actions

Use `const actions = useActions()`.

```ts
actions.ask(prompt: string): void
actions.note(text: string): void
actions.pin(id: string): void
actions.dismiss(id: string): void
actions.setMetric(key: string, value: number | string): void
actions.tag(speaker: string, label: string): void
actions.export(): void
```

## UI Vocabulary

Use only `ui.*`: `Panel`, `Section`, `Grid`, `Row`, `Col`, `Stack`, `Card`, `Stat`, `Table`,
`List`, `Timeline`, `Transcript`, `Chart`, `Badge`, `Tag`, `Button`, `Toggle`, `Tabs`, `Progress`,
`Avatar`, `Markdown`, `Empty`.

Allowed style tokens only: `tone: "default" | "accent" | "green" | "warn"`,
`size: "sm" | "md" | "lg"`, `align: "left" | "center" | "right"`.
Do not pass `className` or `style`.

The kit self-handles empty, loading, missing data, and overflow states:

- pass `loading` for skeletons
- tables, lists, timelines, and transcripts render clean empty placeholders
- long text truncates and collections scroll inside their own frame
- layout primitives constrain width, gaps, and overflow automatically

## Go-Live Gate

Hot reload never swaps blindly. A new view must validate, compile, and mount in a hidden trial render
against the current meeting data. If it fails, the terminal keeps the last-good view and shows a small
dismissible "view update failed; keeping previous" note with details for the agent loop.

## Rules

- Default-export one component. It receives no props.
- Imports are unnecessary. Use the injected globals: `React`, `ui`, `useTranscript`, `useSpeakers`,
  `useEntities`, `useActions`, `actions`, `useState`, `useMemo`, `useEffect`.
- No `useMeeting`, `fetch`, `XMLHttpRequest`, `WebSocket`, `eval`, `Function`, dynamic `import()`,
  `document`, `window`, `globalThis`, `localStorage`, or `dangerouslySetInnerHTML`.
- Use the live meeting feed only. No invented data.
- Side effects must go through `actions`.
