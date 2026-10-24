import { expect, mock, test } from 'claude-code/testing'

const PANE = {
  plugin: 'vexa',
  component: 'Pane',
  requestId: 'vexa-home',
  props: {
    title: 'Vexa',
    isFocused: false,
    bodyColumns: 60,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
} as const

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })

function engine(on: any, connect: unknown, calls: { tool: string; args: unknown }[] = []) {
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa__listener_configure' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('mcp.connect', () => ({ value: connect }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'start_demo_meeting') return { value: text({ platform: 'jitsi', native_meeting_id: 'vexa-demo-1', status: 'requested' }) }
    if (e.tool === 'list_meetings') return { value: text({ meetings: [] }) }
    if (e.tool === 'get_meeting_transcript') {
      const all = [{ segment_id: 's1', completed: true, speaker: 'Vexa demo', text: 'Hello from the demo', absolute_start_time: '2026-10-24T12:00:01Z' }]
      return { value: text({ status: 'active', next_index: all.length, segments: all.slice(Number(e.args?.since_index ?? 0)) }) }
    }
    return { value: text({}) }
  })
}

test('not signed in: the pane shows the sign-in state', async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: false, reason: 'auth', message: 'needs sign-in' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ ...PANE, surface })
    expect((await ui.find({ type: 'Text', text: /Sign in to Vexa/ }))?.text).toMatch(/Sign in/)
    expect(await ui.find({ type: 'Text', text: /\/mcp/ })).toBeTruthy()
    await ui.unmount()
  }
})

test('signed in: Try a demo starts the demo and the transcript follows', async ($, on) => {
  const calls: { tool: string; args: unknown }[] = []
  const clock = mock.clock(on)
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' }, calls)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.press({ key: 'h-demo' })
  expect(calls.map(c => c.tool)).toContain('start_demo_meeting')
  await clock.advance(4000)
  expect((await ui.find({ type: 'Text', text: /Hello from the demo/ }))?.text).toBe('Hello from the demo')
  await ui.unmount()
})

test('tool.check: reads are approved, meeting actions keep their prompt', async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' })
  on('tool.check', () => ({ decision: 'ask' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const read = await $.tool.check({ tool: 'mcp__plugin_vexa_vexa__get_meeting_transcript', input: {} })
  expect(read.decision).toBe('allow')
  for (const tool of ['calendar_status', 'workspace_search']) {
    expect((await $.tool.check({ tool: `mcp__plugin_vexa_vexa__${tool}`, input: {} })).decision).toBe('allow')
  }
  for (const tool of ['speak_in_meeting', 'stop_bot', 'request_meeting_bot', 'set_auto_join', 'workspace_write', 'meeting_bind_workspace', 'calendar_connect']) {
    const r = await $.tool.check({ tool: `mcp__plugin_vexa_vexa__${tool}`, input: {} })
    expect(r.decision).toBe('ask')
  }
  const other = await $.tool.check({ tool: 'mcp__other__get_meeting_transcript', input: {} })
  expect(other.decision).toBe('ask')
})

test('one pane: Calendar and Workspaces are views behind its nav buttons', async ($, on) => {
  mock.clock(on)
  const calls: { tool: string; args: any }[] = []
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  let autoJoin = false
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'calendar_status') return { value: text({ calendars: [{ email: 'me@acme.com' }], upcoming: [{ id: 'ev1', title: 'Acme weekly', start: '2026-10-25T10:00:00Z', auto_join: autoJoin }] }) }
    if (e.tool === 'set_auto_join') { autoJoin = e.args.auto_join; return { value: text({ ok: true }) } }
    if (e.tool === 'workspace_list') return { value: text({ workspaces: [{ id: 'team', name: 'Team', kind: 'shared' }] }) }
    if (e.tool === 'workspace_search') return { value: text({ results: [{ workspace: 'team', path: 'decisions.md', snippet: 'annual plan' }] }) }
    if (e.tool === 'workspace_read') return { value: text({ text: '# Decisions\nAnnual plan at 38k.' }) }
    if (e.tool === 'list_meetings') return { value: text({ meetings: [{ id: 7, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'completed', data: { title: 'Acme pricing call' } }] }) }
    return { value: text({ ok: true }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /note-taker/ })).toBeTruthy()

  await ui.press({ key: 'nav-calendar' })
  expect((await ui.find({ type: 'Text', text: /me@acme\.com/ }))?.text).toMatch(/me@acme/)
  await ui.press({ key: 'aj-ev1' })
  expect(calls.find(c => c.tool === 'set_auto_join')?.args).toEqual({ meeting_id: 'ev1', auto_join: true })

  await ui.press({ key: 'nav-workspaces' })
  expect(await ui.find({ type: 'Text', text: /Team · shared/ })).toBeTruthy()
  await ui.input({ key: 'w-query', text: 'annual' } as any)
  await ui.press({ key: 'res-0' })
  expect(await ui.find({ type: 'Text', text: /Annual plan at 38k/ })).toBeTruthy()

  await ui.press({ key: 'nav-calls' })
  await ui.press({ key: 'call-7' })
  await ui.press({ key: 'nav-workspaces' })
  await ui.press({ key: 'bind-team' })
  expect(calls.find(c => c.tool === 'meeting_bind_workspace')?.args).toEqual({ platform: 'google_meet', native_meeting_id: 'abc-defg-hij', workspace: 'team' })
  await ui.unmount()
})
