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

declare module 'claude-code' {
  interface PluginState {
    vexa: { meeting: Meeting }
  }
}
