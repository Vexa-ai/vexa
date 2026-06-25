export const MANIFEST = `# Meeting Canvas v1

Meeting Canvas is a harness-governed React view. The agent writes \`views/meeting.tsx\`; the terminal validates, transpiles, trial-renders, and only then promotes it in-browser.

## Data Hooks

Use these declarative hooks only. They are scoped to the current meeting automatically, return safe empty defaults, and never expose fetch, WebSocket, DOM, or event-subscription primitives.

\`\`\`ts
const { segments, liveCaption } = useTranscript({ by?: "time" | "speaker", window?: number })
// segments: { speaker?: string; text: string; ts?: number | string }[]

const meeting = useMeeting()
// { meeting: { id, nativeId?, title, status?, startedAt?, participants?, docs? }, transcript, entities, cards, metrics, sections }

const speakers = useSpeakers()
// { name: string; segments: number; talkMs: number; talkPct: number }[]

const entities = useEntities({ kind?: "person" | "company" | "product" | "number" })
// EntityItem[]: { id, kind, name, context?, summary?, quote?, docPath?, researched? }

const signals = useSignals()
// EntityItem[] with kind: "signal", derived from meeting cards and action items.

const docs = useMeetingDocs()
// { brief: { path, present, title? }, report: { path, present, title? } }
\`\`\`

\`useMeetingDocs()\` uses deterministic workspace paths for the active meeting: brief
\`kg/entities/meeting/<meeting-native-or-id>.md\`; report
\`kg/entities/meeting/<meeting-native-or-id>-report.md\`. \`present\` is best-effort and false unless
the meeting snapshot or local canvas state already knows the document exists.

## Actions

\`\`\`ts
const actions = useActions()
actions.ask(prompt: string): void
actions.research(entity: { name: string; kind: string }): void
actions.openDoc(path: string): void
actions.copyRef(token: string): void
actions.note(text: string): void
actions.pin(id: string): void
actions.dismiss(id: string): void
actions.setMetric(key: string, value: number | string): void
actions.tag(speaker: string, label: string): void
actions.export(): void
\`\`\`

\`ask\` posts to the meeting chat session. \`research\` posts a fire-and-forget meeting/research turn that asks the agent to research the entity with web + workspace KG, update the entity doc, and commit. \`openDoc\` opens a workspace doc tab. \`copyRef\` writes to the clipboard. The rest are sanctioned harness effects and update the local canvas state safely.

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
- \`ui.EntityList({ items?: EntityItem[], empty?, loading? })\`
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
- You may use \`React\`, \`ui\`, \`useMeeting\`, \`useTranscript\`, \`useSpeakers\`, \`useEntities\`, \`useSignals\`, \`useMeetingDocs\`, \`useActions\`, \`actions\`, \`useState\`, \`useMemo\`, and \`useEffect\`.
- Imports are unnecessary. If present, imports may only reference React or the harness modules.
- No \`fetch\`, \`XMLHttpRequest\`, \`WebSocket\`, \`eval\`, \`Function\`, dynamic \`import()\`, \`document\`, \`window\`, \`globalThis\`, \`localStorage\`, or \`dangerouslySetInnerHTML\`.
- No arbitrary DOM styling. The kit is theme-locked to terminal CSS variables.
- All side effects go through \`useActions()\`.
`;
