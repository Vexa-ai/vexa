import { expect, mock, test } from 'claude-code/testing'

import { bandState, runsListener, MEETING_CONTEXT_LINK, MEETING_CONTEXT_WORDS, leadingJson, localBand, meetingContext, serverOfCall, uiMessage, vexaServerOf } from '../hooks/register'

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })

// `refuse`: every mod call fails the way auto mode refuses one in the desktop app.
function engine(on: any, connect: unknown | (() => unknown), calls: { tool: string; args: unknown }[] = [], opened: string[] = [], env: Record<string, string> = {}, tools = ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript', 'whats_waiting'], browser = false, refuse = false) {
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa-live__listener_configure' } }))
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

test("finds the person's Vexa by its tools, whatever the server is called", () => {
  const names = ['mcp__claude_ai_Vexa__get_meeting_transcript', 'mcp__claude_ai_Vexa__whats_waiting', 'mcp__other__get_meeting_transcript', 'Bash']
  expect(vexaServerOf(names)).toBe('claude_ai_Vexa')
  expect(vexaServerOf(['mcp__other__get_meeting_transcript'])).toBe('')
  expect(vexaServerOf([])).toBe('')
})

test('no Vexa in this session: nothing is called or drawn; /vexa says to connect it first', async ($, on) => {
  mock.clock(on)
  const calls: { tool: string; args: unknown }[] = []
  const opened: string[] = []
  engine(on, { isConnected: false, reason: 'failed', message: 'none' }, calls, opened, {}, [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(calls).toEqual([])
  expect(opened).toEqual([])
  const r: any = await $.command.run({ command: 'vexa', args: '' } as any)
  expect(r.text).toBe('Connect Vexa first: ask Claude to set up Vexa.')
})

test("a connector's Vexa is used through Claude Code's connection, and told the plugin is here", async ($, on) => {
  const clock = mock.clock(on)
  const calls: { server?: string; tool: string; args: any }[] = []
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa-live__listener_configure' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('fs.read', (_: unknown, e: any) => (String(e.path).endsWith('plugin.json') ? { value: JSON.stringify({ version: '0.1.0-rc' }) } : { deny: 'ENOENT' }))
  on('env.get', () => ({ value: undefined }))
  on('tool.list', () => ({ value: ['get_meeting_transcript', 'whats_waiting', 'list_meetings', 'live_heartbeat'].map(n => ({ name: `mcp__claude_ai_Vexa__${n}` })) }))
  on('mcp.call', (_: unknown, e: any) => { calls.push({ server: e.server, tool: e.tool, args: e.args }); return { value: text({ meetings: [] }) } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(10)
  expect(calls.find(c => c.tool === 'live_heartbeat')).toEqual({ server: 'claude_ai_Vexa', tool: 'live_heartbeat', args: { version: '0.1.0-rc' } })
  await clock.advance(31_500)
  expect(calls.find(c => c.tool === 'list_meetings')?.server).toBe('claude_ai_Vexa')
})

test('a tool answer with a notice after its JSON still parses', () => {
  expect(leadingJson('{"meetings":[{"id":5}]}\n\n⚠ Vexa: 13 items wait in your queue {not json}')).toEqual({ meetings: [{ id: 5 }] })
})

test("tool.check: this plugin's own calls to the person's Vexa pass; the model's are the session's", async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' })
  // Beneath the plugin, a decider that asks for everything.
  on('tool.check', () => ({ decision: 'ask' }))
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const tool of ['get_meeting_transcript', 'speak_in_meeting', 'panel_link']) {
    expect((await $.tool.check({ tool: `mcp__plugin_vexa_vexa__${tool}`, input: {} })).decision).toBe('ask')
  }
  expect((await $.tool.check({ tool: 'mcp__other__get_meeting_transcript', input: {} })).decision).toBe('ask')
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
  on('session.id', () => ({ value: 's-test' }))
  on('fs.read', (_: unknown, e: any) => (String(e.path).endsWith('/.vexa/link/owner') ? { value: 's-test' } : { deny: 'ENOENT' }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: ['claude_requests_next', 'claude_requests_ack', 'get_meeting_transcript', 'whats_waiting'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
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

test('the web panel: /vexa opens a one-time sign-in link in the browser pane', async ($, on) => {
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
  on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vexa_vexa__panel_link' }, { name: 'mcp__plugin_vexa_vexa__get_meeting_transcript' }, { name: 'mcp__plugin_vexa_vexa__whats_waiting' }, { name: 'mcp__Claude_Browser__preview_start' }] }))
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
  // Only the plugin's own call for the panel origin is approved; anyone else's preview_start is not ours to allow.
  expect((await $.tool.check({ tool: 'mcp__Claude_Browser__preview_start', input: { url: 'https://claude-rc-app.dev.vexa.ai/panel' } })).decision).toBe('ask')
})

test('a call Claude starts from the chat opens in the web panel at that meeting', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on)
  const calls: { tool: string; args: any }[] = []
  const opened: string[] = []
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' }, calls, opened, {}, ['start_demo_meeting', 'panel_link', 'get_meeting_transcript', 'whats_waiting'], true)
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

test("a connector whose tools are deferred is found by the model's first call to it", async ($, on) => {
  const clock = mock.clock(on)
  const calls: { server?: string; tool: string }[] = []
  const uuid = 'cbaaef3d-c1de-4a5b-9ac0-29235c68ec0d'
  expect(serverOfCall(`mcp__${uuid}__whats_waiting`)).toBe(uuid)
  expect(serverOfCall('mcp__vexa-live__listener_configure')).toBe('')
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa-live__listener_configure' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('tool.list', () => ({ value: [{ name: 'Bash' }] }))
  on('tool.call', () => ({ result: {}, text: JSON.stringify({ waiting: 0, items: [] }) }))
  on('mcp.call', (_: unknown, e: any) => { calls.push({ server: e.server, tool: e.tool }); return { value: text({ meetings: [] }) } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  expect(calls).toEqual([])
  await $.tool.call({ tool: `mcp__${uuid}__whats_waiting` } as any)
  await clock.advance(31_500)
  expect(calls.find(c => c.tool === 'list_meetings')?.server).toBe(uuid)
})

test("the plugin's own tools answer under its own name", async ($, on) => {
  mock.clock(on)
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const r: any = await $.tool.call({ tool: 'mcp__vexa-live__listener_configure', action: 'status' } as any)
  expect(JSON.parse(r.result)).toMatchObject({ listening: false, preset: 'default' })
})

test('two Vexa servers in one session: the one with the live meeting is followed', async ($, on) => {
  const clock = mock.clock(on)
  const calls: { server?: string; tool: string; args: any }[] = []
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa-live__listener_configure' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('tool.list', () => ({ value: ['vexa-appdev', 'cbaaef3d-c1de'].flatMap(s => ['get_meeting_transcript', 'whats_waiting', 'list_meetings'].map(n => ({ name: `mcp__${s}__${n}` }))) }))
  on('mcp.call', (_: unknown, e: any) => {
    calls.push({ server: e.server, tool: e.tool, args: e.args })
    if (e.tool === 'list_meetings') {
      return { value: text({ meetings: e.server === 'cbaaef3d-c1de' ? [{ id: 106, platform: 'zoom', native_meeting_id: '8312', status: 'active', data: { title: 'Founder call' } }] : [] }) }
    }
    if (e.tool === 'get_meeting_transcript') return { value: text({ status: 'active', segments: [], next_index: 0 }) }
    return { value: text({}) }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  expect(calls.filter(c => c.tool === 'list_meetings').map(c => c.server)).toEqual(['vexa-appdev', 'cbaaef3d-c1de'])
  await clock.advance(1_600)
  expect(calls.filter(c => c.tool === 'get_meeting_transcript').every(c => c.server === 'cbaaef3d-c1de')).toBe(true)
  expect(calls.some(c => c.tool === 'get_meeting_transcript')).toBe(true)
})


test("the band reads the script's files: Listening with the titles, the trigger for 10 s, Answering, Not listening", () => {
  const now = 1_000_000_000
  const call = { title: 'Pricing sync', native_meeting_id: '881', state: 'listening', trigger: '', wake_ts: 0, ts: now - 5000 }
  expect(localBand([], now, false).any).toBe(false)
  expect(localBand([call, { ...call, title: '', native_meeting_id: 'abc-defg-hij' }], now, false).line).toBe('Listening · Pricing sync, abc-defg-hij')
  const woke = { ...call, state: 'woke', trigger: 'what did we', wake_ts: now - 3000 }
  expect(localBand([woke], now, false)).toEqual({ any: true, isOn: true, hot: true, line: 'what did we' })
  expect(localBand([{ ...woke, wake_ts: now - 20_000 }], now, true).line).toBe('Answering')
  expect(localBand([{ ...woke, wake_ts: now - 20_000 }], now, false).line).toBe('Listening · Pricing sync')
  expect(localBand([{ ...call, state: 'ended' }], now, false)).toEqual({ any: true, isOn: false, hot: false, line: 'Pricing sync · Not listening' })
  expect(localBand([{ ...call, ts: now - 11 * 60_000 }], now, false).any).toBe(false)
})


test('a press in the web panel reaches the chat as one line and a vexa-ui:v1 reference', () => {
  const text = uiMessage({ id: 'ab', action: 'update_notes', label: 'Update notes', text: 'Update the notes on the page of this call.',
    meeting_id: '107', params: { platform: 'google_meet', native_id: 'jgm-injt-gkj', page: 'kg/entities/meeting/x.md' }, created_ms: 1792944000000 }, 'testing 23')
  const [line] = text.split('\n')
  expect(line).toBe('Vexa UI: the person pressed «Update notes» on testing 23.')
  const ref = JSON.parse(text.split('```vexa-ui:v1\n')[1]!.split('\n```')[0]!)
  expect(ref).toEqual({ action: 'update_notes', label: 'Update notes', meeting: { id: 107, platform: 'google_meet', native_id: 'jgm-injt-gkj' },
    page: 'kg/entities/meeting/x.md', from: 'panel', at: new Date(1792944000000).toISOString() })
  expect(text).toContain('Update the notes on the page of this call.')
})


test('a message about meetings carries one line for the model; anything else carries none', () => {
  expect(meetingContext('https://meet.google.com/gaf-ejgk-yna')).toBe(MEETING_CONTEXT_LINK)
  expect(meetingContext('join https://us02web.zoom.us/j/73792195000?pwd=x')).toBe(MEETING_CONTEXT_LINK)
  expect(meetingContext('встреча с Иваном завтра')).toBe(MEETING_CONTEXT_WORDS)
  expect(meetingContext('что говорил Петя про цену?')).toBe(MEETING_CONTEXT_WORDS)
  expect(meetingContext('What did Ana say about pricing on the call?')).toBe(MEETING_CONTEXT_WORDS)
  expect(meetingContext('fix the failing test in parser.ts')).toBeNull()
  expect(meetingContext('рефакторинг модуля оплаты')).toBeNull()
})


test('the band says what is true: six states, each with its next action', () => {
  const base = { connected: true, owner: true, linkActive: true, linkState: 'running', call: 'Acme pricing', listening: true, flash: '', answering: false, preset: 'default' }
  expect(bandState({ ...base, connected: false, linkActive: false })).toMatchObject({ line: 'Vexa not connected', buttons: ['connect'] })
  expect(bandState({ ...base, call: '', listening: false })).toMatchObject({ tone: 'off', line: 'Vexa · no live call', buttons: ['open', 'send'] })
  expect(bandState(base)).toMatchObject({ tone: 'live', line: 'Listening · Acme pricing', buttons: ['open', 'stop'] })
  expect(bandState({ ...base, listening: false })).toMatchObject({ tone: 'warn', line: 'Not listening · Acme pricing', buttons: ['listen', 'open'] })
  expect(bandState({ ...base, owner: false })).toMatchObject({ line: 'Listening in another chat', buttons: ['here'] })
  expect(bandState({ ...base, linkState: 'expired' })).toMatchObject({ tone: 'warn', line: 'Line to Vexa paused', buttons: ['reconnect'] })
  expect(bandState({ ...base, linkActive: false, owner: false, polling: true })).toMatchObject({ tone: 'live' })
  expect(bandState({ ...base, flash: 'what did we' }).line).toBe('what did we')
})

test('only a command that runs vexa-link claims the line, never one that names it', () => {
  expect(runsListener('VEXA_LISTEN_KEY=abc sh "/x/plugins/vexa-live/bin/vexa-link" https://claude-rc.dev.vexa.ai')).toBe(true)
  expect(runsListener('VEXA_LISTEN_KEY=$K sh /p/bin/vexa-listen https://a vexa_call c-1 0 "t"')).toBe(true)
  expect(runsListener('grep -n vexa-link clients/claude-plugin/bin/README.md')).toBe(false)
  expect(runsListener('cat ~/.vexa/link/owner; ls bin/vexa-link')).toBe(false)
  expect(runsListener('git log --oneline -- clients/claude-plugin/bin/vexa-link')).toBe(false)
})
