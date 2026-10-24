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
  on('tool.list', () => ({ value: ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
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
    expect((await ui.find({ type: 'Text', text: /Not signed in/ }))?.text).toBe('Not signed in. Ask Claude to set up Vexa.')
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
  // Beneath the plugin, an auto-mode decider that would allow speaking and leaving.
  on('tool.check', (_: unknown, e: any) => ({ decision: /speak_in_meeting|stop_bot/.test(e.tool) ? 'allow' : 'ask' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const read = await $.tool.check({ tool: 'mcp__plugin_vexa_vexa__get_meeting_transcript', input: {} })
  expect(read.decision).toBe('allow')
  for (const tool of ['calendar_status', 'workspace_search']) {
    expect((await $.tool.check({ tool: `mcp__plugin_vexa_vexa__${tool}`, input: {} })).decision).toBe('allow')
  }
  for (const tool of ['speak_in_meeting', 'stop_bot']) {
    const r = await $.tool.check({ tool: `mcp__plugin_vexa_vexa__${tool}`, input: {} })
    expect(r.decision).toBe('ask')
  }
  for (const tool of ['request_meeting_bot', 'set_auto_join', 'workspace_write', 'meeting_bind_workspace']) {
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
  on('tool.list', () => ({ value: ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
  let autoJoin = false
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'calendar_status') return { value: text({ calendars: [{ id: 'c1', name: 'me@acme.com', enabled: true, auto_join: true }], window_days: 7, upcoming: [{ meeting_db_id: 230, title: 'Acme weekly', scheduled_at: '2026-10-25T10:00:00Z', platform: 'google_meet', native_meeting_id: 'abc-defg-hij', auto_join: autoJoin, will_auto_join: autoJoin, why_not: autoJoin ? null : 'auto-join is off for this meeting' }], will_auto_join: autoJoin ? 1 : 0 }) }
    if (e.tool === 'set_auto_join') { autoJoin = e.args.enabled; return { value: text({ scope: 'meeting', meeting: {} }) } }
    if (e.tool === 'workspace_list') return { value: text({ workspaces: [{ slug: 'team', name: 'team', display: 'Team', kind: 'shared', role: 'owner', state: 'active' }, { slug: 'old', name: 'Old', display: 'Old', state: 'inactive' }], line: 'Writes to Personal' }) }
    if (e.tool === 'workspace_tree') return { value: text({ files: ['README.md', 'decisions.md', 'logo.png'] }) }
    if (e.tool === 'workspace_read') return { value: text({ path: e.args.path, content: e.args.path === 'README.md' ? '# Team\nOur notes.' : '# Decisions\nAnnual plan at 38k.' }) }
    if (e.tool === 'list_meetings') return { value: text({ meetings: [{ id: 7, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'completed', data: { title: 'Acme pricing call' } }] }) }
    return { value: text({ ok: true }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /Send Vexa to a call/ })).toBeTruthy()

  await ui.press({ key: 'nav-calendar' })
  expect((await ui.find({ type: 'Text', text: /me@acme\.com/ }))?.text).toMatch(/me@acme/)
  expect(await ui.find({ type: 'Text', text: /auto-join is off for this meeting/ })).toBeTruthy()
  await ui.press({ key: 'aj-230' })
  expect(calls.find(c => c.tool === 'set_auto_join')?.args).toEqual({ enabled: true, meeting_db_id: 230 })

  await ui.press({ key: 'nav-workspaces' })
  expect(await ui.find({ type: 'Text', text: /shared · owner/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /^Old$/ })).toBeFalsy()
  await ui.press({ key: 'ws-team-b' })
  expect(calls.find(c => c.tool === 'workspace_tree')?.args).toEqual({ slug: 'team' })
  expect(calls.filter(c => c.tool === 'workspace_read').map(c => c.args)).toEqual([{ slug: 'team', path: 'README.md' }])
  await ui.press({ key: 'ws-f-decisions.md' })
  expect(calls.at(-1)?.args).toEqual({ slug: 'team', path: 'decisions.md' })

  await ui.press({ key: 'nav-calls' })
  await ui.press({ key: 'call-7-b' })
  expect((await ui.find({ type: 'Text', text: /^Past call$/ }))?.text).toBe('Past call')
  expect(calls.some(c => c.tool === 'get_meeting_transcript' && c.args.since_index === 0)).toBe(true)
  await ui.press({ key: 'nav-workspaces' })
  await ui.press({ key: 'ws-team-b' })
  await ui.press({ key: 'ws-bind' })
  expect(calls.find(c => c.tool === 'meeting_bind_workspace')?.args).toEqual({ meeting_db_id: 7, workspace: 'team' })
  await ui.unmount()
})

test('a hosted server without calendar, workspace or demo tools: those views and buttons stay hidden', async ($, on) => {
  mock.clock(on)
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__get_meeting_transcript' }] }))
  const calls: string[] = []
  on('mcp.call', (_: unknown, e: any) => { calls.push(e.tool); return { value: text({ meetings: [] }) } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /Send Vexa to a call/ })).toBeTruthy()
  for (const t of [/^Workspaces$/, /^Calendar$/, /Try a demo/]) expect(await ui.find({ type: 'Text', text: t })).toBeFalsy()
  expect(calls).not.toContain('workspace_list')
  await ui.unmount()
})

test('setup in chat: the context asks the next question, vexa_settings saves the answer, a finished call is filed', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on)
  const calls: { tool: string; args: any }[] = []
  const prompts: string[] = []
  let status = 'active'
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: '/home/u' }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: ['workspace_list', 'get_meeting_transcript'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
  on('prompt.submit', (_: unknown, e: any) => { prompts.push(e.text); return { text: e.text } })
  on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'engine', scope: 'shared' }] }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'workspace_list') return { value: text({ workspaces: [{ slug: 'team', display: 'Team', kind: 'shared', role: 'owner', state: 'active' }], line: 'Writes to Team' }) }
    if (e.tool === 'request_meeting_bot') return { value: text({ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active' }) }
    if (e.tool === 'get_meeting_transcript') return { value: text({ status, segments: [], next_index: 0 }) }
    return { value: text({ ok: true }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(10)
  const before: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  expect(before.sections.at(-1).text).toMatch(/Where should I keep what I learn/)

  const r: any = await $.tool.call({ tool: 'mcp__vexa__vexa_settings', write: 'cloud', workspace: 'team' } as any)
  expect(r.result.saved).toEqual({ write: 'cloud', workspace: 'team', localPath: '' })
  expect(calls.find(c => c.tool === 'workspace_defaults_set')?.args).toEqual({ target: 'team' })
  const after: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  expect(after.sections.at(-1).text).toMatch(/workspace «team»/)
  expect(after.sections.at(-1).text).toMatch(/workspace_read CLAUDE\.md/)

  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  status = 'completed'
  await clock.advance(1_500)
  await clock.advance(1_500)
  const filed = prompts.filter(p => p.includes('just ended'))
  expect(filed.length).toBe(1)
  expect(filed[0]).toMatch(/meeting_db_id 41/)
  expect(filed[0]).toMatch(/vexa-workspace skill/)
  await ui.unmount()
})

test('web → chat: requests the web queued become turns and are acked; the poll is the heartbeat', async ($, on) => {
  const clock = mock.clock(on)
  const calls: { tool: string; args: any }[] = []
  const prompts: string[] = []
  let queue = [{ id: 'r1', text: 'Summarize the call «Acme weekly» for me.', meeting_id: 41, created_ms: 1 }]
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: ['claude_requests_next', 'claude_requests_ack'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
  on('prompt.submit', (_: unknown, e: any) => { prompts.push(e.text); return { text: e.text } })
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (e.tool === 'claude_requests_next') { const requests = queue; queue = []; return { value: text({ requests, present_until_ms: 30000 }) } }
    return { value: text({ ok: true, meetings: [] }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(1_000)
  expect(prompts).toEqual(['[Vexa panel] Summarize the call «Acme weekly» for me.'])
  expect(calls.find(c => c.tool === 'claude_requests_ack')?.args).toEqual({ id: 'r1', outcome: 'submitted' })
  expect(calls.find(c => c.tool === 'claude_requests_next')?.args).toEqual({ wait_ms: 8000 })
  expect(calls.filter(c => c.tool === 'claude_requests_next').length).toBeGreaterThan(1)
})
