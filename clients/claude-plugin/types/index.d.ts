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

declare module 'claude-code' {
  interface PluginState {
    vexa: { meeting: Meeting; listener: Listener }
  }
}
