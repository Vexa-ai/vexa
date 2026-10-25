import { expect, mock, test } from 'claude-code/testing'

import { leadingJson, vexaUrlOf } from '../hooks/register'

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })

// `refuse`: every mod call fails the way auto mode refuses one in the desktop app.
function engine(on: any, connect: unknown | (() => unknown), calls: { tool: string; args: unknown }[] = [], opened: string[] = [], env: Record<string, string> = {}, tools = ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript'], browser = false, refuse = false) {
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa__listener_configure' } }))
  on('ui.open', (_: unknown, e: any) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('mcp.connect', () => ({ value: typeof connect === 'function' ? connect() : connect }))
  on('tool.list', () => ({ value: [...tools.map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })), ...(browser ? [{ name: 'mcp__Claude_Browser__preview_start' }] : [])] }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', (_: unknown, e: any) => ({ value: env[e.name] }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ tool: e.tool, args: e.args })
    if (refuse) return { value: { isError: true, content: [{ type: 'text', text: 'The server-side auto mode classifier gave no verdict' }] } }
    if (e.tool === 'panel_link') return { value: text({ url: 'https://claude-rc-app.dev.vexa.ai/api/auth/redeem?t=SECRET&next=/panel' }) }
    if (e.tool === 'start_demo_meeting') return { value: text({ platform: 'jitsi', native_meeting_id: 'vexa-demo-1', status: 'requested' }) }
    if (e.tool === 'list_meetings') return { value: text({ meetings: [] }) }
    if (e.tool === 'get_meeting_transcript') {
      const all = [{ segment_id: 's1', completed: true, speaker: 'Vexa demo', text: 'Hello from the demo', absolute_start_time: '2026-10-24T12:00:01Z' }]
      return { value: text({ status: 'active', next_index: all.length, segments: all.slice(Number(e.args?.since_index ?? 0)) }) }
    }
    return { value: text({}) }
  })
}

test('not signed in: the plugin draws nothing of its own; /vexa says to ask Claude', async ($, on) => {
  mock.clock(on)
  const opened: string[] = []
  const calls: { tool: string; args: unknown }[] = []
  engine(on, { isConnected: false, reason: 'auth', message: 'needs sign-in' }, calls, opened)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(opened).toEqual([])
  expect(calls).toEqual([])
  const r: any = await $.command.run({ command: 'vexa', args: '' } as any)
  expect(r.text).toBe("Vexa isn't signed in yet. Ask Claude to set up Vexa.")
  expect(opened).toEqual([])
})

test('signed in: nothing is called and nothing opens at startup until a meeting is live; a refused call shows nothing', async ($, on) => {
  const clock = mock.clock(on)
  const opened: string[] = []
  const calls: { tool: string; args: unknown }[] = []
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' }, calls, opened, {}, undefined, false, true)
  on('ui.toast', (_: unknown, e: any) => { opened.push(`toast:${e.text ?? e}`); return { value: undefined } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(calls).toEqual([])
  expect(opened).toEqual([])
  await clock.advance(60_000)
  expect(calls.length).toBeGreaterThan(0)
  expect(opened).toEqual([])
  // The band draws nothing of ours: no offer, no error text.
  await expect($.ui.mount({ plugin: 'vexa', component: 'AbovePrompt', surface: 'desktop', props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 10 }, view: {} } } as any)).rejects.toThrow(/no implementation/)
})

test("a company's own Vexa: the address comes from the manifest's literal server URL", async () => {
  expect(vexaUrlOf(JSON.stringify({ mcpServers: { vexa: { url: 'https://vexa.acme.example/mcp' } } }))).toBe('https://vexa.acme.example')
  expect(vexaUrlOf(JSON.stringify({ mcpServers: { vexa: { url: '${VEXA_URL}/mcp' } } }))).toBe('https://api.cloud.vexa.ai')
  expect(vexaUrlOf('not json')).toBe('https://api.cloud.vexa.ai')
})

test('a tool answer with a notice after its JSON still parses', () => {
  expect(leadingJson('{"meetings":[{"id":5}]}\n\n⚠ Vexa: 13 items wait in your queue {not json}')).toEqual({ meetings: [{ id: 5 }] })
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
  on('tool.call', (_: unknown, e: any) => (e.tool === 'mcp__plugin_vexa_vexa__request_meeting_bot'
    ? { result: {}, text: JSON.stringify({ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active' }) }
    : { result: {} }))
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

  await $.tool.call({ tool: 'mcp__plugin_vexa_vexa__request_meeting_bot', meeting_url: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  status = 'completed'
  await clock.advance(1_500)
  await clock.advance(1_500)
  const filed = prompts.filter(p => p.includes('just ended'))
  expect(filed.length).toBe(1)
  expect(filed[0]).toMatch(/meeting_db_id 41/)
  expect(filed[0]).toMatch(/vexa-workspace skill/)
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
  expect(opened).toEqual([])
  expect(calls.find(c => c.tool === 'preview_start')).toMatchObject({ args: { url: expect.stringContaining('/api/auth/redeem?t=SECRET') } })
  expect((await $.tool.check({ tool: 'mcp__plugin_vexa_vexa__panel_link', input: {} })).decision).toBe('deny')
  // Only the plugin's own call for the panel origin is approved; anyone else's preview_start is not ours to allow.
  expect((await $.tool.check({ tool: 'mcp__Claude_Browser__preview_start', input: { url: 'https://claude-rc-app.dev.vexa.ai/panel' } })).decision).toBe('ask')
})

test('a call Claude starts from the chat opens in the web panel at that meeting', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on)
  const calls: { tool: string; args: any }[] = []
  const opened: string[] = []
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' }, calls, opened, {}, ['start_demo_meeting', 'panel_link'], true)
  on('tool.call', (_: unknown, e: any) => (e.tool === 'mcp__plugin_vexa_vexa__start_demo_meeting'
    ? { result: {}, text: JSON.stringify({ id: 300, platform: 'vexa_call', native_meeting_id: 'call-d7be66819bdd', status: 'requested', data: { demo: true } }) }
    : { result: {} }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await $.tool.call({ tool: 'mcp__plugin_vexa_vexa__start_demo_meeting' } as any)
  await clock.advance(10)
  expect(calls.find(c => c.tool === 'panel_link')?.args).toEqual({ next: '/panel/meeting/vexa_call/call-d7be66819bdd' })
  expect(calls.find(c => c.tool === 'preview_start')).toBeTruthy()
  expect(opened).toEqual([])
})

test('sign-in from the chat: Claude offers it and calls authenticate; when it lands the web panel opens', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on)
  let signedIn = false
  const calls: { tool: string; args: any }[] = []
  const opened: string[] = []
  engine(on, () => signedIn ? { isConnected: true, server: 'plugin:vexa:vexa' } : { isConnected: false, reason: 'auth', message: 'needs sign-in' }, calls, opened, {}, ['panel_link', 'authenticate'], true)
  on('prompt.compose', () => ({ sections: [] }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ctx: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['terminal'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  expect(ctx.sections.at(-1).text).toMatch(/Vexa is installed\. Sign in\?/)
  expect(ctx.sections.at(-1).text).toMatch(/call mcp__plugin_vexa_vexa__authenticate/)
  expect((await $.tool.check({ tool: 'mcp__plugin_vexa_vexa__authenticate', input: {} })).decision).toBe('allow')
  expect(ctx.sections.at(-1).text).not.toMatch(/type \/mcp/)
  signedIn = true
  await clock.advance(5000)
  expect(calls.find(c => c.tool === 'preview_start')).toBeTruthy()
  expect(opened).toEqual([])
})

test('the desktop app has no authenticate tool: Claude sends the person to /mcp in one line, never to an API key', async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: false, reason: 'auth', message: 'needs sign-in' })
  on('prompt.compose', () => ({ sections: [] }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ctx: any = await $.prompt.compose({ model: 'claude-sonnet-5-5', promptModel: 'claude-sonnet-5-5', surfaces: ['desktop'], tools: [], outputStyle: null, traits: [], sections: [] } as any)
  const t = ctx.sections.at(-1).text
  expect(t).toMatch(/Vexa is installed\. Sign in\?/)
  expect(t).toMatch(/Type \/mcp here, choose plugin:vexa:vexa and sign in/)
  expect(t).not.toMatch(/call mcp__plugin_vexa_vexa__authenticate/)
  expect(t).not.toMatch(/API key at|VEXA_API_KEY/)
})
