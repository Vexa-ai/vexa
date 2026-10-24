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

function engine(on: any, connect: unknown | (() => unknown), calls: { tool: string; args: unknown }[] = [], opened: string[] = []) {
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa__listener_configure' } }))
  on('ui.open', (_: unknown, e: any) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('mcp.connect', () => ({ value: typeof connect === 'function' ? connect() : connect }))
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

test("a company's own Vexa: the sign-in names its host and drops hosted-only copy", { options: { vexa_url: 'https://vexa.acme.example' } }, async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: false, reason: 'auth', message: 'needs sign-in' })
  on('prompt.compose', () => ({ sections: [] }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /Your company's Vexa: vexa\.acme\.example/ })).toBeTruthy()
  const ctx: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  expect(ctx.sections.at(-1).text).toMatch(/company's Vexa vexa\.acme\.example/)
  expect(await ui.find({ type: 'Text', text: /Google or Microsoft/ })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /free credit/ })).toBeFalsy()
  await ui.unmount()
})

test('hosted Vexa by default: the hosted sign-in copy', async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: false, reason: 'auth', message: 'needs sign-in' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /Not signed in/ })).toBeTruthy()
  expect(await ui.find({ type: 'Text', text: /Your company's Vexa/ })).toBeFalsy()
  await ui.unmount()
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

  const r: any = { result: JSON.parse(((await $.tool.call({ tool: 'mcp__vexa__vexa_settings', write: 'cloud', workspace: 'team' } as any)) as any).result) }
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

test('a call another person\'s bot is already in: the pane says whose, and leaving never stops their bot', async ($, on) => {
  const clock = mock.clock(on)
  const calls: string[] = []
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__get_meeting_transcript' }] }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push(e.tool)
    if (e.tool === 'request_meeting_bot') return { value: text({ id: 52, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active', attached: true, attached_via: 'share', owner: { user_id: 9, email: 'ana@acme.io' }, message: 'Joined ana@acme.io\'s bot in this call.' }) }
    if (e.tool === 'get_meeting_transcript') return { value: text({ status: 'active', segments: [], next_index: 0 }) }
    return { value: text({ meetings: [] }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /shared with you/ }))?.text).toBe("ana@acme.io's bot · shared with you")
  expect((await ui.find({ type: 'Text', text: /Stop following/ }))?.text).toBe('Stop following')
  await ui.press({ key: 'stop' })
  expect(calls).not.toContain('stop_bot')
  await ui.unmount()
})

test('a tool answer with a notice after its JSON still parses; a timed-out Send finds the bot it did send', async ($, on) => {
  const clock = mock.clock(on)
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__get_meeting_transcript' }] }))
  const withNotice = (o: unknown) => ({ content: [{ type: 'text', text: `${JSON.stringify(o)}\n\n⚠ Vexa: 13 items wait in your queue {not json}` }] })
  on('mcp.call', (_: unknown, e: any) => {
    if (e.tool === 'list_meetings') return { value: withNotice({ meetings: [{ id: 5, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'requested', data: { title: 'Acme pricing call' } }] }) }
    if (e.tool === 'request_meeting_bot') return { value: { isError: true, content: [{ type: 'text', text: 'HTTP 504 upstream ReadTimeout' }] } }
    if (e.tool === 'parse_meeting_link') return { value: text({ platform: 'google_meet', native_meeting_id: 'abc-defg-hij' }) }
    if (e.tool === 'get_meeting_transcript') return { value: text({ status: 'requested', segments: [], next_index: 0 }) }
    return { value: text({}) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(10)
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /No calls yet/ })).toBeFalsy()
  expect(await ui.find({ type: 'Button', key: 'h-rec-5-b' } as any)).toBeTruthy()
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  expect(await ui.find({ type: 'Text', text: /504/ })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /^requested$/ })).toBeTruthy()
  await ui.unmount()
})

test('the web panel: /vexa opens a one-time sign-in link in the browser pane; the model may not mint one', async ($, on) => {
  mock.clock(on)
  mock.store(on)
  const calls: { server?: string; tool: string; args: any }[] = []
  const opened: string[] = []
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', (_: unknown, e: any) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__panel_link' }, { name: 'mcp__Claude_Browser__preview_start' }] }))
  on('command.run', (_: unknown, e: any) => ({ text: '' }))
  on('tool.check', () => ({ decision: 'ask' }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ server: e.server, tool: e.tool, args: e.args })
    if (e.tool === 'panel_link') return { value: text({ url: 'https://claude-rc-app.dev.vexa.ai/api/auth/redeem?t=SECRET&next=/panel', expires_at: 1 }) }
    return { value: text({ meetings: [] }) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(opened).not.toContain('vexa-home')
  await $.command.run({ command: 'vexa', args: '' } as any)
  expect(calls.find(c => c.tool === 'panel_link')?.args).toEqual({ next: '/panel' })
  expect(calls.find(c => c.tool === 'preview_start')).toMatchObject({ args: { url: expect.stringContaining('/api/auth/redeem?t=SECRET') } })
  expect((await $.tool.check({ tool: 'mcp__plugin_vexa_vexa__panel_link', input: {} })).decision).toBe('deny')
  // Only the plugin's own call for the panel origin is approved; anyone else's preview_start is not ours to allow.
  expect((await $.tool.check({ tool: 'mcp__Claude_Browser__preview_start', input: { url: 'https://claude-rc-app.dev.vexa.ai/panel' } })).decision).toBe('ask')
})

test('a demo Claude starts from the chat: the pane follows it and names it', async ($, on) => {
  mock.clock(on)
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__start_demo_meeting' }] }))
  on('mcp.call', () => ({ value: text({ meetings: [], segments: [], status: 'requested' }) }))
  on('tool.call', (_: unknown, e: any) => (e.tool === 'mcp__plugin_vexa_vexa__start_demo_meeting'
    ? { result: {}, text: JSON.stringify({ id: 300, platform: 'vexa_call', native_meeting_id: 'call-d7be66819bdd', status: 'requested', data: { demo: true } }) }
    : { result: {} }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ ...PANE, surface: 'desktop' })
  await $.tool.call({ tool: 'mcp__plugin_vexa_vexa__start_demo_meeting' } as any)
  expect((await ui.find({ type: 'Text', text: /^Demo meeting$/ }))?.text).toBe('Demo meeting')
  expect(await ui.find({ type: 'Text', text: /call-d7be66819bdd/ })).toBeFalsy()
  await ui.unmount()
})

test('sign-in from the chat: Claude offers it and runs claude mcp login; when it lands the panel opens', async ($, on) => {
  const clock = mock.clock(on)
  let signedIn = false
  const opened: string[] = []
  engine(on, () => signedIn ? { isConnected: true, server: 'plugin:vexa:vexa' } : { isConnected: false, reason: 'auth', message: 'needs sign-in' }, [], opened)
  on('prompt.compose', () => ({ sections: [] }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ctx: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  expect(ctx.sections.at(-1).text).toMatch(/Sign in\? I'll open your browser/)
  expect(ctx.sections.at(-1).text).toMatch(/claude mcp login plugin:vexa:vexa/)
  expect(ctx.sections.at(-1).text).not.toMatch(/type \/mcp/)
  opened.length = 0
  signedIn = true
  await clock.advance(5000)
  expect(opened).toContain('vexa-home')
})
