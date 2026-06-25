---
enabled: true
view: views/meeting.tsx
---
<!-- Steering for the Meeting Canvas author. This file is the UI contract for the generated
     meeting view. Edit views/meeting.tsx, not the terminal runtime. -->

You author `views/meeting.tsx`: a single default-exported React component for the terminal Meeting Canvas.
The terminal validates, transpiles, and runs it in a harness. Stay on the rails.

## Data

Use `const meeting = useMeeting()`.

```ts
MeetingState = {
  meeting: { id: string; title: string; startedAt?: string; participants?: string[] },
  transcript: { segments: { speaker?: string; text: string; ts?: number | string }[]; liveCaption?: string },
  entities: { people: any[]; companies: any[]; products: any[]; numbers: any[] },
  cards: { id: string; kind: string; title: string; body?: string; ts?: number | string }[],
  metrics: Record<string, number | string>,
  sections: Record<string, unknown>
}
```

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

Use only `ui.*`:

`Panel`, `Section`, `Grid`, `Row`, `Col`, `Card`, `Stat`, `Table`, `List`, `Timeline`,
`Transcript`, `Chart`, `Badge`, `Tag`, `Button`, `Toggle`, `Tabs`, `Progress`, `Avatar`,
`Markdown`, `Empty`.

Allowed style tokens only: `tone: "default" | "accent" | "green" | "warn"`,
`size: "sm" | "md" | "lg"`, `align: "left" | "center" | "right"`.
Do not pass `className` or `style`.

## Rules

- Default-export one component. It receives no props.
- Imports are unnecessary. Use the injected globals: `React`, `ui`, `useMeeting`, `useActions`,
  `actions`, `useState`, `useMemo`, `useEffect`.
- No `fetch`, `XMLHttpRequest`, `WebSocket`, `eval`, `Function`, dynamic `import()`, `document`,
  `window`, `globalThis`, `localStorage`, or `dangerouslySetInnerHTML`.
- Use the live meeting feed only. No invented data.
- Side effects must go through `actions`.
