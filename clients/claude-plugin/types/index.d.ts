export type Seg = { speaker: string; text: string; time: string }

// `connection`: where the plugin's own `vexa` server stands. `signin` is the
// OAuth sign-in Claude Code has not done yet; `refused` carries the reason.
export type Meeting = {
  connection: 'connecting' | 'signin' | 'refused' | 'ready'
  url: string
  platform: string
  nativeId: string
  isDemo: boolean
  status: string
  nextIndex: number
  segments: Seg[]
  error: string
}

// The listen brief, as signals.v1 `Brief` names it (labels, focus, known, wake_at, max_wait_s,
// rules), so the app's chat and Claude share one rule model.
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
export type Wake = { at: string; reason: string; excerpt: string }

// The app the pane and listener run from (vexa-app.json), as the pane shows it.
export type AppInfo = {
  name: string
  version: string
  source: string
  upstream: string
  presets: string[]
  pane: { title: string; emptyHint: string; demo: boolean; offerListening: boolean }
}

// One listener: offered when a meeting is live, running only after the person pressed Listen.
export type Listener = {
  offer: Room | null
  declined: string[]
  active: Room | null
  preset: string
  brief: Brief
  nextIndex: number
  context: Line[]
  buffer: Line[]
  score: number
  since: number
  lastWakeAt: number
  wakes: Wake[]
  error: string
  app: AppInfo
}

declare module 'claude-code' {
  interface PluginState {
    vexa: { meeting: Meeting; listener: Listener }
  }
}
