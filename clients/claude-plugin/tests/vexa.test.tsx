import { expect, mock, test } from 'claude-code/testing'

import { AFTER_CALL_PAGE, afterCallAsk, inThisCallAsk, inThisCallDue, inThisCallKnobs, inThisCallTiming, bandState, fromPerson, heardLine, panelShownHere, signalIndex, pickOwner, runsListener, withChat, FOLLOW_UP_CONTEXT, MEETING_CONTEXT_LINK, MEETING_CONTEXT_WORDS, PERSON_CONTEXT, leadingJson, localBand, meetingContext, serverOfCall, uiMessage, vexaServerOf } from '../hooks/register'

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })

// `refuse`: every mod call fails the way auto mode refuses one in the desktop app.
function engine(on: any, connect: unknown | (() => unknown), calls: { tool: string; args: unknown }[] = [], opened: string[] = [], env: Record<string, string> = {}, tools = ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript', 'whats_waiting'], browser = false, refuse = false, answer?: (e: any) => unknown) {
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
    const own = answer?.(e)
    if (own) return { value: own }
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

test('no Vexa in this session: nothing is called or drawn; /vexa itself starts the sign-in', async ($, on) => {
  mock.clock(on)
  const calls: { tool: string; args: unknown }[] = []
  const opened: string[] = []
  const ran: string[][] = []
  engine(on, { isConnected: false, reason: 'failed', message: 'none' }, calls, opened, {}, [])
  on('http.fetch', (_: unknown, e: any) => (/\/pair$/.test(e.url)
    ? { value: { ok: true, status: 200, headers: {}, text: JSON.stringify({ code: 'abcd2345', setup_url: 'https://app/panel/setup?code=abcd2345', signin_url: 'https://app/signin?code=abcd2345' }) } }
    : { value: { ok: false, status: 404, headers: {}, text: '' } }))
  on('process.run', (_: unknown, e: any) => { ran.push([...e.argv]); return { value: { exitCode: 0, stdout: e.argv[0] === 'uname' ? 'Darwin\n' : '', stderr: '' } } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(calls).toEqual([])
  expect(opened).toEqual([])
  const r: any = await $.command.run({ command: 'vexa', args: '' } as any)
  expect(r.text).toBe('Vexa: sign in with Google in the browser window that just opened; /vexa carries on when you are in.')
  expect(ran).toContainEqual(['open', 'https://app/signin?code=abcd2345'])
})

test('/vexa: the panel opens, the line starts with a listen key, the live call is listened to here; /vexa stop stops', async ($, on) => {
  mock.clock(on)
  mock.store(on)
  const calls: { tool: string; args: any }[] = []
  const spawned: any[] = []
  engine(on, { isConnected: true, server: 'plugin:vexa:vexa' }, calls, [], { HOME: '/home/u' }, ['panel_link', 'listen_token', 'get_meeting_transcript', 'list_meetings', 'whats_waiting'], true, false, (e: any) => {
    if (e.tool === 'listen_token') return text({ key: 'vxa_listen_K', api_url: 'https://claude-rc.dev.vexa.ai' })
    if (e.tool === 'list_meetings') return text({ meetings: [{ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active', data: { title: 'Acme pricing call' } }] })
    return undefined
  })
  on('fs.write', () => ({ value: undefined }))
  on('fs.list', () => ({ value: [] }))
  on('process.spawn', async function* (_: unknown, e: any) { spawned.push(e); return { value: { code: 0, signal: null } } })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const r: any = await $.command.run({ command: 'vexa', args: '' } as any)
  expect(calls.find(c => c.tool === 'panel_link')?.args.next).toMatch(/^\/panel(\/meeting\/google_meet\/abc-defg-hij)?\?surface=claude-panel/)
  expect(spawned).toHaveLength(1)
  expect(spawned[0].argv.slice(0, 1)).toEqual(['sh'])
  expect(spawned[0].argv[1]).toMatch(/\/bin\/vexa-link$/)
  expect(spawned[0].argv[2]).toBe('https://claude-rc.dev.vexa.ai')
  expect(spawned[0].env).toEqual({ VEXA_LISTEN_KEY: 'vxa_listen_K' })
  expect(r.text).toMatch(/^Vexa: panel open · line from Vexa started · /)
  expect(r.text).not.toContain('vxa_listen_K')
  const stop: any = await $.command.run({ command: 'vexa', args: 'stop' } as any)
  expect(stop.text).toBe('Vexa: stopped listening in this chat.')
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
  on('fs.read', (_: unknown, e: any) => (String(e.path).endsWith('/.vexa/link/used/s-test') ? { value: '1' } : { deny: 'ENOENT' }))
  on('fs.list', (_: unknown, e: any) => (String(e.path).endsWith('/.vexa/link/used') ? { value: [{ name: 's-test', kind: 'file', size: 1, mtimeMs: 0, isLink: false }] } : { value: [] }))
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
  expect(calls.find(c => c.tool === 'panel_link')?.args).toEqual({ next: '/panel?surface=claude-panel' })
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
  expect(text).toMatch(/Do the work first/)
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
  expect(bandState({ ...base, linkState: 'revoked' })).toMatchObject({ tone: 'warn', line: 'Line to Vexa paused', buttons: ['reconnect'] })
  // a Vexa that does not answer is waited for: nothing to press, and never «Listening» meanwhile
  expect(bandState({ ...base, linkState: 'reconnecting' })).toMatchObject({ tone: 'warn', line: 'Reconnecting to Vexa…', buttons: ['open'] })
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


test('the band shows the listener at work: a pulse while lines arrive, what it caught while it acts', () => {
  const base = { connected: true, owner: true, linkActive: true, linkState: 'running', call: 'Claude Code mods video', listening: true, flash: '', answering: false, preset: 'default' }
  expect(bandState({ ...base, hearing: true })).toMatchObject({ tone: 'hear', line: 'Listening · Claude Code mods video' })
  expect(bandState(base).tone).toBe('live')
  const caught = heardLine('Клод, ты меня слышишь?', 'Клод', true)
  expect(caught).toBe('Heard: “Клод, ты меня слышишь?” → answering')
  expect(bandState({ ...base, flash: caught, answering: true })).toMatchObject({ tone: 'react', line: 'Answering: “Клод, ты меня слышишь?”' })
  expect(heardLine('a very long line that keeps going well past what the band can hold in one row', 'x', false)).toMatch(/^Caught: .{47}… → checking$/)
})


test('which chat Vexa talks to: the most recently used one among those with the panel open, else the most recently used', () => {
  const used = { a: 100, b: 300, c: 200 }
  expect(pickOwner(used, [])).toBe('b')
  expect(pickOwner(used, ['a', 'c'])).toBe('c')
  expect(pickOwner(used, ['a'])).toBe('a')
  expect(pickOwner(used, ['someone-else'])).toBe('b')
  expect(pickOwner({}, ['a'])).toBe('')
  expect(withChat('/panel?surface=claude-panel', 's-1')).toBe('/panel?surface=claude-panel&chat=s-1')
  expect(withChat('/panel', 's-1')).toBe('/panel?chat=s-1')
  expect(withChat('/panel?chat=x', 's-1')).toBe('/panel?chat=x')
})

test('a panel Vexa says is open in this chat on this page is not opened again', () => {
  const path = '/panel/meeting/google_meet/qkd-cxvb-jgj'
  const panels = { ts: 1000, panels: [{ chat: 'eb1c', path }] }
  expect(panelShownHere(panels, 2000, 'eb1c', path)).toBe(true)
  expect(panelShownHere(panels, 2000, 'eb1c', '/panel')).toBe(false)
  expect(panelShownHere(panels, 2000, 'other', path)).toBe(false)
  expect(panelShownHere(panels, 1000 + 90_000, 'eb1c', path)).toBe(false)
  expect(panelShownHere(null, 2000, 'eb1c', path)).toBe(false)
  expect(panelShownHere(panels, 2000, '', path)).toBe(false)
})


test('a chat counts as used only by what the person sends, whatever the app calls its origin', () => {
  expect(fromPerson('composer', 'fix the test')).toBe(true)
  expect(fromPerson('sdk', 'https://meet.google.com/abc-defg-hij')).toBe(true)
  expect(fromPerson('bridge', 'встреча завтра')).toBe(true)
  expect(fromPerson('sdk', '<cross-session-message from="local_x">deploy done</cross-session-message>')).toBe(false)
  expect(fromPerson('sdk', '<task-notification><task-id>b1</task-id></task-notification>')).toBe(false)
  expect(fromPerson('sdk', '[Vexa listener] Heard …')).toBe(false)
  expect(fromPerson('sdk', 'Vexa UI: the person pressed «Summarize».')).toBe(false)
  expect(fromPerson('sdk', '<system-reminder>Linked sessions now: meeting-notes = local_1 (idle)</system-reminder>')).toBe(false)
  expect(fromPerson('sdk', 'The app was quit while you were working. Please continue from where you left off.')).toBe(false)
  expect(fromPerson('sdk', '[SYSTEM NOTIFICATION - NOT USER INPUT] the meeting agent finished')).toBe(false)
  expect(fromPerson('sdk', 'This session is being continued from a previous conversation about the call.')).toBe(false)
  expect(fromPerson('composer', '<command-name>/vexa</command-name>')).toBe(false)
  expect(fromPerson('sdk', 'Tool loaded.')).toBe(false)
  expect(fromPerson('peer', 'hello')).toBe(false)
  expect(fromPerson('composer', 'x', true)).toBe(false)
})


test('a person or company asked about by name, and a follow-up, carry their own line', () => {
  expect(meetingContext('Who is Leo?')).toBe(PERSON_CONTEXT)
  expect(meetingContext('quick one — what about Infobip, did they reply?')).toBe(PERSON_CONTEXT)
  expect(meetingContext('Ana said the launch slips, can you check')).toBe(PERSON_CONTEXT)
  expect(meetingContext('кто такой Иван Осман?')).toBe(PERSON_CONTEXT)
  expect(meetingContext('Write a follow-up to Leo')).toBe(FOLLOW_UP_CONTEXT)
  expect(meetingContext('draft an email to Ana about the pricing')).toBe(FOLLOW_UP_CONTEXT)
  expect(meetingContext('rename the variable in parser.ts')).toBeNull()
  expect(MEETING_CONTEXT_LINK).toMatch(/BEFORE loading any skill/)
  expect(MEETING_CONTEXT_LINK).toMatch(/never paste the link/)
})


test('a chat working with Vexa keeps the wakes over a chat that only typed last', () => {
  const used = { worker: 100, chatter: 300 }
  expect(pickOwner(used, [], ['worker'])).toBe('worker')
  expect(pickOwner(used, ['chatter'], ['worker'])).toBe('chatter')
  expect(pickOwner(used, [], [])).toBe('chatter')
  expect(pickOwner({ a: 1, b: 2 }, [], ['a', 'b'])).toBe('b')
})


test('the band never says listening while a wake waits; Listen here moves the line at once', () => {
  const base = { connected: true, owner: true, linkActive: true, linkState: 'running', call: 'gqo-mkdv-dzx', listening: true, flash: '', answering: false, preset: 'default' }
  expect(bandState({ ...base, busy: true })).toMatchObject({ line: 'Listening · gqo-mkdv-dzx' })
  const waiting = bandState({ ...base, busy: true, stage: 'queued', flash: 'Heard: “Клод, что ты слышишь” → answering', heardAt: '21:25' })
  expect(waiting).toMatchObject({ tone: 'warn', line: 'Heard: “Клод, что ты слышишь” 21:25 · waiting' })
  expect(bandState({ ...base, answering: true, stage: 'answering', flash: 'Heard: “Клод, что ты слышишь” → answering' }).line).toBe('Answering: “Клод, что ты слышишь”')
  expect(pickOwner({ coordinator: 300, onboarding: 100 }, ['coordinator'], ['coordinator'], 'onboarding')).toBe('onboarding')
  expect(pickOwner({ coordinator: 300 }, [], [], 'gone-chat')).toBe('coordinator')
})

test('«In this call» is asked for as the call moves on, on the page\'s own timing', () => {
  const k = inThisCallKnobs('---\nlabel: in-this-call\n# when\nlines: 4\nevery_s: 90\nsoon_s: 30\n---\n[in this call] …')
  expect(k).toEqual({ lines: 4, everyMs: 90_000, soonMs: 30_000 })
  expect(inThisCallKnobs('no frontmatter')).toBeNull()
  expect(inThisCallKnobs('---\nlines: 4\nevery_s: 90\n---\n')).toBeNull()        // all three or none
  const at = (o: Partial<Parameters<typeof inThisCallDue>[0]>) => inThisCallDue({ lines: 1, hit: false, lastAt: 0, now: 1_000_000, busy: false, ...o }, k)
  expect(at({ lines: 4 })).toBe(true)
  expect(at({ lines: 3 })).toBe(false)                                        // not enough talk yet
  expect(at({ lines: 50, lastAt: 1_000_000 - 90_000 + 1 })).toBe(false)       // asked a moment ago
  expect(at({ hit: true, lastAt: 1_000_000 - 30_000 })).toBe(true)            // a new name or a decision
  expect(at({ hit: true, lastAt: 1_000_000 - 1000 })).toBe(false)
  expect(at({ lines: 50, busy: true })).toBe(false)                           // never over a turn
  expect(at({ lines: 0, hit: true })).toBe(false)
})

test('without the page there is no timing, and why says so: nothing stands in for it', () => {
  const page = '---\nlines: 6\nevery_s: 120\nsoon_s: 60\n---\n'
  expect(inThisCallTiming(JSON.stringify({ path: 'asks/in-this-call.md', content: page }))).toEqual({ knobs: { lines: 6, everyMs: 120_000, soonMs: 60_000 } })
  expect(inThisCallTiming(JSON.stringify({ error: 'this Vexa has no _global asks/in-this-call.md (404)' }))).toEqual({ why: 'this Vexa has no _global asks/in-this-call.md (404)' })
  expect(inThisCallTiming(JSON.stringify({ content: 'no frontmatter' }))).toEqual({ why: 'no timing in its frontmatter' })
  expect(inThisCallTiming('not json')).toEqual({ why: 'unreadable' })
  const band = (inThisCallOff: string) => bandState({ connected: true, owner: true, linkActive: true, linkState: 'running', call: 'Pilot', listening: true, flash: '', answering: false, preset: 'default', inThisCallOff })
  expect(band('unreadable')).toMatchObject({ tone: 'warn', line: 'Listening · Pilot · «In this call» off: unreadable' })
  expect(band('')).toMatchObject({ tone: 'live', line: 'Listening · Pilot' })
})

test('the wake points at the _global page and carries what was heard, never the format', () => {
  const ask = inThisCallAsk('DNA Dev Check-in', { names: ['Olga Avramenko'], decisions: ['we decided to wait for V1'] })
  expect(ask).toContain('_global asks/in-this-call.md')
  expect(ask).toContain('workspace_read slug "_global", path "asks/in-this-call.md"')
  expect(ask).toContain('new names: Olga Avramenko')
  expect(ask).toContain('possible decisions: we decided to wait for V1')
  for (const format of ['**Who:**', 'HH:MM', 'section_update', 'entity_upsert', 'eight', 'sub-bullet', 'Decided']) expect(ask).not.toContain(format)
  expect(inThisCallAsk('x', { names: [], decisions: [] })).not.toContain('new names')
})

test('the end of a call points at the _global after-call page, never the steps', () => {
  const ask = afterCallAsk('Northwind pilot', 'google_meet abc-defg-hij')
  expect(ask).toContain(`${AFTER_CALL_PAGE.slug} ${AFTER_CALL_PAGE.path}`)
  expect(ask).toContain('ended')
  for (const step of ['entity_upsert', 'annotate_meeting', 'section_update', 'links_report', 'Action items', 'Decided']) expect(ask).not.toContain(step)
})

test("a signal points at get_meeting_transcript only with the lines' own place there", () => {
  const s = (id: string, index?: number) => ({ id, speaker: 'A', text: 't', time: '', pending: false, ...(index === undefined ? {} : { index }) })
  expect(signalIndex([s('a', 438), s('b', 439)])).toBe(438)
  expect(signalIndex([s('a', 438), s('b')])).toBe(null)
  expect(signalIndex([s('262:440')])).toBe(null)
  expect(signalIndex([])).toBe(null)
})
