// A transcript segment. `pending`: a draft the recognizer may still change or withdraw.
export type Seg = { id: string; speaker: string; text: string; time: string; pending: boolean }

// `connection`: where the plugin's own `vexa` server stands. `signin` is the
// OAuth sign-in Claude Code has not done yet; `refused` carries the reason.
export type Meeting = {
  connection: 'connecting' | 'signin' | 'refused' | 'ready'
  url: string
  platform: string
  nativeId: string
  title: string
  isDemo: boolean
  status: string
  nextIndex: number
  // The server's live feed (get_meeting_transcript include_pending): its cursor, and whether this
  // deployment serves one; without it, confirmed lines are read with since_index.
  liveCursor: string
  liveFeed: boolean
  segments: Seg[]
  error: string
}

// The listen brief, as signals.v1 `Brief` names it (labels, focus, known, wake_at, max_wait_s,
// rules), so the app's chat and Claude share one rule model. Here `focus` carries the
// plain-language rules the attention filter reads; `rules` are keyword/person/regex triggers.
export type Label = { name: string; question?: string; wake?: boolean }
export type Rule = { kind: 'keyword' | 'person' | 'regex'; value: string; wake?: boolean }
export type Brief = {
  labels: Label[]
  focus: string
  known: string
  wake_at: number
  max_wait_s: number
  rules: Rule[]
}

export type Room = { platform: string; nativeId: string; title: string }
export type Line = { speaker: string; text: string }
export type Wake = { at: string; reason: string }

// The app the pane and listener run from (vexa-app.json), as the pane shows it.
export type AppInfo = {
  name: string
  version: string
  source: string
  upstream: string
  presets: string[]
  pane: { title: string; emptyHint: string; demo: boolean; offerListening: boolean }
}

// The listener: offered when a meeting is live, on only after the person pressed Listen; it
// follows the meeting the pane shows.
export type Listener = {
  offer: Room | null
  declined: string[]
  isOn: boolean
  preset: string
  brief: Brief
  triaged: string[]
  lastWakeAt: number
  wakes: Wake[]
  lastVerdict: string
  lastMs: number
  error: string
  app: AppInfo
}

// The one Vexa pane's views, and what the views beyond the meeting read.
export type View = { name: 'home' | 'meeting' | 'calls' | 'calendar' | 'workspaces' }
export type Call = { id: string; platform: string; nativeId: string; status: string; title: string; at: string }
export type Calls = { items: Call[]; filter: string; error: string }
export type Upcoming = { id: string; title: string; start: string; platform: string; nativeId: string; autoJoin: boolean }
export type Calendar = { connected: string[]; upcoming: Upcoming[]; link: string; error: string; loaded: boolean }
export type Workspace = { id: string; name: string; kind: string }
export type Page = { path: string; snippet: string; workspace: string }
export type Workspaces = {
  items: Workspace[]
  query: string
  results: Page[]
  open: { workspace: string; path: string; text: string } | null
  bound: string
  error: string
  loaded: boolean
}

declare module 'claude-code' {
  interface PluginState {
    vexa: { meeting: Meeting; listener: Listener; view: View; calls: Calls; calendar: Calendar; workspaces: Workspaces }
  }
}
