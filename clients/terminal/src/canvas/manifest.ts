export const MANIFEST = `# Meeting Canvas v1

Meeting Canvas is a harness-governed React view. The agent writes \`views/meeting.tsx\`; the terminal validates, transpiles, trial-renders, and only then promotes it in-browser.

## Data Hooks

Use these declarative hooks only. They are scoped to the current meeting automatically, return safe empty defaults, and never expose fetch, WebSocket, DOM, or event-subscription primitives.

\`\`\`ts
const { segments, liveCaption } = useTranscript({ by?: "time" | "speaker", window?: number })
// segments: { speaker?: string; text: string; ts?: number | string }[]

const speakers = useSpeakers()
// { name: string; segments: number; talkMs: number; talkPct: number }[]

const entities = useEntities({ kind?: "person" | "company" | "product" | "number" })
// { kind, title, subtitle?, body?, value? }[]
\`\`\`

Do not call \`useMeeting\` from generated views. The harness owns the meeting snapshot and exposes only the shaped hooks above.

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

The kit is hardened for non-technical authors:

- empty collections render themed placeholders automatically
- every component accepts \`loading?: boolean\` for a subtle skeleton state
- missing or undefined data props default safely
- long text is constrained with ellipsis
- tables, lists, timelines, and transcripts scroll inside their own frame
- layout primitives constrain width, spacing, and overflow

- \`ui.Panel({ title?, subtitle?, tone?, loading?, children })\`
- \`ui.Section({ title?, loading?, children })\`
- \`ui.Grid({ columns?: 1|2|3|4|"auto", size?, loading?, children })\`
- \`ui.Row({ align?, size?, loading?, children })\`
- \`ui.Col({ size?, loading?, children })\`
- \`ui.Stack({ size?, loading?, children })\`
- \`ui.Card({ title?, body?, ts?, tone?, loading?, children })\`
- \`ui.Stat({ label?, value?, delta?, tone?, size?, loading? })\`
- \`ui.Table({ columns?, rows?, empty?, loading? })\`
- \`ui.List({ items?, empty?, loading? })\`
- \`ui.Timeline({ items?, empty?, loading? })\`
- \`ui.Transcript({ segments?, liveCaption?, empty?, loading? })\`
- \`ui.Chart({ kind?: "bar"|"line", data?, tone?, loading? })\`
- \`ui.Badge({ tone?, loading?, children? })\`
- \`ui.Tag({ tone?, loading?, children? })\`
- \`ui.Button({ tone?, size?, disabled?, loading?, onClick?, children? })\`
- \`ui.Toggle({ checked?, label?, loading?, onChange? })\`
- \`ui.Tabs({ tabs?, value?, loading?, onChange? })\`
- \`ui.Progress({ value?, max?, tone?, label?, loading? })\`
- \`ui.Avatar({ name?, tone?, size?, loading? })\`
- \`ui.Markdown({ children?: string, loading? })\`
- \`ui.Empty({ title?, body? })\`

## Go-Live Gate

Hot reload never swaps blindly. New \`views/meeting.tsx\` source must pass validator, compile, and mount in a hidden trial render against the current meeting data. Only a successful trial render is promoted to the visible canvas. If validation, compile, or render fails, the terminal keeps the last-good view and shows a small dismissible "view update failed; keeping previous" note with details for the agent loop.

## Rules

- Default-export one React component. It receives no props.
- You may use \`React\`, \`ui\`, \`useTranscript\`, \`useSpeakers\`, \`useEntities\`, \`useActions\`, \`actions\`, \`useState\`, \`useMemo\`, and \`useEffect\`.
- Imports are unnecessary. If present, imports may only reference React or the harness modules.
- No \`useMeeting\`, \`fetch\`, \`XMLHttpRequest\`, \`WebSocket\`, \`eval\`, \`Function\`, dynamic \`import()\`, \`document\`, \`window\`, \`globalThis\`, \`localStorage\`, or \`dangerouslySetInnerHTML\`.
- No arbitrary DOM styling. The kit is theme-locked to terminal CSS variables.
- All side effects go through \`useActions()\`.
`;
