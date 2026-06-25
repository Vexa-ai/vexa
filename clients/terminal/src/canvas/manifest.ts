export const MANIFEST = `# Meeting Canvas v1

Meeting Canvas is a harness-governed React view. The agent writes \`views/meeting.tsx\`; the terminal validates, transpiles, and runs it in-browser.

## Data

\`\`\`ts
MeetingState = {
  meeting: { id: string; title: string; startedAt?: string; participants?: string[] },
  transcript: { segments: { speaker?: string; text: string; ts?: number | string }[]; liveCaption?: string },
  entities: { people: any[]; companies: any[]; products: any[]; numbers: any[] },
  cards: { id: string; kind: string; title: string; body?: string; ts?: number | string }[],
  metrics: Record<string, number | string>,
  sections: Record<string, unknown>
}
\`\`\`

Use \`const meeting = useMeeting()\`. It is live and re-renders as transcript, cards, metrics, tags, pins, and dismissals change.

## Actions

\`\`\`ts
const actions = useActions()
actions.ask(prompt: string): void
actions.note(text: string): void
actions.pin(id: string): void
actions.dismiss(id: string): void
actions.setMetric(key: string, value: number | string): void
actions.tag(speaker: string, label: string): void
actions.export(): void
\`\`\`

\`ask\` posts to the meeting chat session. The rest are sanctioned harness effects and update the local canvas state safely.

## UI Kit

Only \`ui.*\` components are allowed. They accept data props plus token props only: \`tone: "default" | "accent" | "green" | "warn"\`, \`size: "sm" | "md" | "lg"\`, and \`align: "left" | "center" | "right"\` where listed. No \`className\`, no \`style\`.

- \`ui.Panel({ title?, subtitle?, tone?, children })\`
- \`ui.Section({ title?, children })\`
- \`ui.Grid({ columns?: 1|2|3|4|"auto", size?, children })\`
- \`ui.Row({ align?, size?, children })\`
- \`ui.Col({ size?, children })\`
- \`ui.Card({ title?, body?, ts?, tone?, children })\`
- \`ui.Stat({ label, value, delta?, tone?, size? })\`
- \`ui.Table({ columns?, rows, empty? })\`
- \`ui.List({ items, empty? })\`
- \`ui.Timeline({ items, empty? })\`
- \`ui.Transcript({ segments, liveCaption?, empty? })\`
- \`ui.Chart({ kind?: "bar"|"line", data, tone? })\`
- \`ui.Badge({ tone?, children })\`
- \`ui.Tag({ tone?, children })\`
- \`ui.Button({ tone?, size?, disabled?, onClick?, children })\`
- \`ui.Toggle({ checked, label?, onChange? })\`
- \`ui.Tabs({ tabs, value?, onChange? })\`
- \`ui.Progress({ value, max?, tone?, label? })\`
- \`ui.Avatar({ name, tone?, size? })\`
- \`ui.Markdown({ children: string })\`
- \`ui.Empty({ title?, body? })\`

## Rules

- Default-export one React component. It receives no props.
- You may use \`React\`, \`ui\`, \`useMeeting\`, \`useActions\`, \`actions\`, \`useState\`, \`useMemo\`, and \`useEffect\`.
- Imports are unnecessary. If present, imports may only reference React or the harness modules.
- No \`fetch\`, \`XMLHttpRequest\`, \`WebSocket\`, \`eval\`, \`Function\`, dynamic \`import()\`, \`document\`, \`window\`, \`globalThis\`, \`localStorage\`, or \`dangerouslySetInnerHTML\`.
- No arbitrary DOM styling. The kit is theme-locked to terminal CSS variables.
- All side effects go through \`useActions()\`.
`;
