import { expect, mock, test } from 'claude-code/testing'

import { applyLive, mergeSegs } from '../hooks/register'
import { FALLBACK_APP, briefFor, buildApp, calibrationSummary, isDirect, normalize, parseDecision, parseManifest, ruleHit, rulesText, triggerWords } from '../hooks/listener'

const BAND = {
  plugin: 'vexa-live',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const
const FOOTER = { plugin: 'vexa-live', component: 'SessionMode', props: { modes: [] } } as const

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })
const seg = (id: string, speaker: string, t: string, completed = true) => ({ segment_id: id, speaker, text: t, completed, absolute_start_time: '2026-10-24T12:00:00Z' })

type Seen = { args: { tool: string; args: any }[]; tools: string[]; model: string[]; prompts: string[]; written: Record<string, string>; annotations: any[]; maps: string[]; upserts: any[]; logs: string[]; triage: string[]; pages: any[] }
const meta: Record<string, unknown> = {}
const endedLive = { value: false }

// A bot Claude sends from the chat: the plugin follows that meeting.
async function sendBot($: any): Promise<void> {
  await $.tool.call({ tool: 'mcp__plugin_vexa_vexa__request_meeting_bot', meeting_url: 'https://meet.google.com/abc-defg-hij' } as any)
}

function engine(on: any, segments: () => unknown[], decision = 'S', fs: Record<string, string> = {}, live?: (() => unknown[]) | 'strict', triage = 'YES check the annual plan'): Seen {
  const seen: Seen = { args: [], tools: [], model: [], prompts: [], written: {}, annotations: [], maps: [], upserts: [], logs: [], triage: [], pages: [] }
  on('ui.log', (_: unknown, e: any) => { seen.logs.push(String(e.text ?? e)); return { value: undefined } })
  for (const k of Object.keys(meta)) delete meta[k]
  endedLive.value = false
  // The engine's own drawing, for a band or footer the plugin passes on.
  on('ui.render', ($: any, e: any) => { const { Text } = $.ui.resolve(e); return <Text>engine</Text> })
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', () => ({ value: undefined }))
  on('tool.call', (_: unknown, e: any) => (e.tool === 'mcp__plugin_vexa_vexa__request_meeting_bot'
    ? { result: {}, text: JSON.stringify({ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active' }) }
    : { result: {} }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('env.get', (_: unknown, e: any) => ({ value: e.name === 'HOME' ? '/home/u' : undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript', 'whats_waiting'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
  on('mcp.call', (_: unknown, e: any) => {
    seen.tools.push(e.tool)
    seen.args.push({ tool: e.tool, args: e.args })
    if (e.tool === 'list_meetings') return { value: text({ meetings: endedLive.value ? [] : [{ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active', data: { title: 'Acme pricing call' } }] }) }
    if (e.tool === 'get_meeting_transcript' && e.args?.include_pending && live === 'strict') {
      return { value: { isError: true, content: [{ type: 'text', text: "Input validation error: Additional properties are not allowed ('include_pending', 'live_wait_ms' were unexpected)" }] } }
    }
    if (e.tool === 'get_meeting_transcript' && e.args?.include_pending && live) {
      return { value: text({ status: 'active', segments: [], live: { events: live(), cursor: 'c', ended: false } }) }
    }
    if (e.tool === 'get_meeting_transcript') {
      const all = segments()
      const from = Number(e.args?.since_index ?? 0)
      return { value: text({ status: endedLive.value ? 'completed' : 'active', since_index: from, next_index: all.length, segments: all.slice(from) }) }
    }
    // Every Vexa carries the _global page that times «In this call».
    if (e.tool === 'workspace_read' && e.args?.slug === '_global' && e.args?.path === 'asks/in-this-call.md') {
      return { value: text({ path: e.args.path, content: '---\nlabel: in-this-call\nlines: 6\nevery_s: 120\nsoon_s: 60\n---\n' }) }
    }
    if (e.tool === 'workspace_write') { seen.pages.push(e.args); return { value: text({ path: e.args.path, written: true }) } }
    if (e.tool === 'entity_upsert') { seen.upserts.push(e.args); return { value: text({ path: e.args.kind === 'meeting' ? 'kg/entities/meeting/x.md' : `kg/entities/${e.args.kind}/y.md` }) } }
    if (e.tool === 'annotate_meeting') { Object.assign(meta, e.args.metadata); seen.annotations.push({ ...e.args.metadata }); return { value: text({ ok: true }) } }
    if (e.tool === 'get_meeting') return { value: text({ id: 41, data: { metadata: { ...meta } } }) }
    if (e.tool === 'request_meeting_bot') return { value: text({ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active' }) }
    return { value: text({}) }
  })
  on('model.complete', (_: unknown, e: any) => {
    const sys = JSON.stringify(e.system ?? '')
    if (sys.includes('knowledge map')) {
      seen.maps.push(String(e.prompt))
      return { value: { isAnswered: true, text: JSON.stringify({
        people: [{ name: 'Ann Lee', role: 'Procurement lead', org: 'Acme' }], companies: [{ name: 'Acme', what: 'renewing for a year' }],
        relations: [{ from: 'Ann Lee', rel: 'works at', to: 'Acme' }], decisions: ['Annual plan at 38k'], numbers: [{ what: 'price', value: '38k' }],
        open_questions: ['Who is Zed?'], readme: '# Acme renewal\nAnn Lee ([[Acme]]) agreed the annual plan at 38k.',
      }), usage: { inputTokens: 900, outputTokens: 300 } } }
    }
    if (sys.includes('deserves the attention')) {
      seen.triage.push(String(e.prompt))
      return { value: { isAnswered: true, text: triage, usage: { inputTokens: 200, outputTokens: 10 } } }
    }
    seen.model.push(String(e.prompt))
    return { value: { isAnswered: true, text: decision, usage: { inputTokens: 1, outputTokens: 1 } } }
  })
  on('prompt.submit', (_: unknown, e: any) => { seen.prompts.push(e.text); return { text: e.text } })
  // The plugin's own folder is spelled PLUGIN here: the kit sets the real root.
  const key = (p: string) => (p.startsWith('/home/') || p.includes('.vexa/app') ? p : `PLUGIN${p.slice(p.lastIndexOf('/app'))}`)
  // This test session owns the line from Vexa unless the test says another session does.
  on('session.id', () => ({ value: 's-test' }))
  const files: Record<string, string> = { '/home/u/.vexa/link/used/s-test': '1', ...fs }
  const dirOf = (p: string) => Object.keys(files).filter(f => f.startsWith(`${key(p)}/`))
  on('fs.exists', (_: unknown, e: any) => ({ value: key(e.path) in files || dirOf(e.path).length > 0 }))
  on('fs.read', (_: unknown, e: any) => (key(e.path) in files ? { value: files[key(e.path)] } : { deny: `ENOENT ${e.path}` }))
  on('fs.write', (_: unknown, e: any) => { files[key(e.path)] = e.text; seen.written[key(e.path)] = e.text; return { value: undefined } })
  on('fs.list', (_: unknown, e: any) => {
    const names = new Map<string, 'file' | 'dir'>()
    for (const f of dirOf(e.path)) {
      const rest = f.slice(key(e.path).length + 1)
      const [head, ...tail] = rest.split('/')
      names.set(head!, tail.length ? 'dir' : 'file')
    }
    return { value: [...names].map(([name, kind]) => ({ name, kind, size: 0, mtimeMs: 0, isLink: false })) }
  })
  return seen
}

test('a live meeting of theirs is listened to by default, silently; Stop stops it and it stays stopped', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [seg('1', 'Ann', 'Our budget is 40k')])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...FOOTER, surface: 'desktop' })
  // At rest Vexa is one green dot in the footer; Stop lives in the panel and in the listener's own control.
  expect((await ui.find({ type: 'Text', text: /^[●◉]$/ }))).toBeTruthy()
  // …and the band above the prompt draws nothing of Vexa's.
  const band = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(await band.find({ type: 'Text', text: 'engine' })).toBeTruthy()
  await band.unmount()
  expect(seen.annotations.at(-1)).toMatchObject({ listener_state: 'listening' })
  expect(seen.prompts).toEqual([])
  await $.tool.call({ tool: 'mcp__vexa-live__listener_configure', action: 'stop' } as any)
  expect(seen.annotations.at(-1)).toMatchObject({ listener_state: 'off' })
  await clock.advance(64_000)
  expect(await ui.find({ type: 'Text', text: /^[●◉]$/ })).toBeFalsy()
  await ui.unmount()
})

test('after Listen, a new line the filter flags wakes the main Claude once; the gap holds the next', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  const seen = engine(on, () => lines, 'W annual plan decision')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  expect(seen.annotations.at(-1)).toMatchObject({ listener_state: 'listening' })
  expect(seen.model.length).toBe(0)
  lines = [...lines, seg('2', 'Ann', 'We decided to go with the annual plan.')]
  await clock.advance(1_500)
  expect(seen.model.at(-1)).toMatch(/annual plan/)
  expect(seen.model.at(-1)).not.toMatch(/NEW:\nAnn: Morning/)
  expect(seen.prompts.filter(p => p.includes('annual plan decision')).length).toBe(1)
  const signal = seen.prompts.find(p => p.includes('annual plan decision'))!
  expect(signal).toMatch(/^\[Vexa listener · signal\] annual plan decision: check the annual plan — google_meet abc-defg-hij/)
  expect(seen.triage.length).toBe(1)
  expect(signal).toMatch(/since_index=1/)
  expect(signal).not.toMatch(/We decided to go with the annual plan/)
  expect((await ui.find({ type: 'Text', text: /^Caught: / }))?.text).toBe('Caught: We decided to go with the annual plan. → checking')
  expect(await ui.find({ type: 'Text', text: /Claude|Heard/ })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /woke|filter|Mode/ })).toBeFalsy()
  lines = [...lines, seg('3', 'Bob', 'And we decided on a 3 year term.')]
  await clock.advance(3_000)
  expect(seen.prompts.filter(p => p.includes('annual plan decision')).length).toBe(1)
  await ui.unmount()
})

test('drafts: replaced in place by their final version, a withdrawn draft goes, a live-feed retract removes one', () => {
  const draft = mergeSegs([], [{ id: 'a', speaker: 'Ann', text: 'we decid', time: '', pending: true }, { id: 'b', speaker: 'Bob', text: 'uh', time: '', pending: true }])
  expect(mergeSegs(draft, [{ id: 'a', speaker: 'Ann', text: 'We decided.', time: '', pending: false }]).map(x => x.text)).toEqual(['We decided.'])
  const live = applyLive([], [{ type: 'transcript', id: 'a', speaker: 'Ann', text: 'we deci', completed: false }, { type: 'transcript', id: 'b', speaker: 'Bob', text: 'hm', completed: false }])
  expect(applyLive(live, [{ type: 'transcript', id: 'a', speaker: 'Ann', text: 'We decided.', completed: true }, { type: 'retract', segment_ids: ['b'] }]).map(x => x.text)).toEqual(['We decided.'])
})

test('a server whose transcript tool refuses the live-feed arguments falls back to confirmed reads', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [seg('a', 'Maya', 'Hi everyone!')], undefined, {}, 'strict')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await sendBot($)
  await clock.advance(1_500)
  await clock.advance(1_500)
  const reads = seen.args.filter(c => c.tool === 'get_meeting_transcript')
  expect(reads.some(c => c.args?.include_pending)).toBe(true)
  expect(reads.at(-1)?.args?.include_pending).toBeFalsy()
})

test('web and plugin share the listener through meeting metadata: state, heartbeat, the web\'s Stop', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [seg('1', 'Ann', 'Morning all.')])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await sendBot($)
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect(seen.annotations.at(-1)).toMatchObject({ listener_state: 'listening', listener_mode: 'normal' })
  const n = seen.annotations.length
  await clock.advance(24_000)
  expect(seen.annotations.length).toBeGreaterThan(n)
  expect(seen.annotations.at(-1).listener_state).toBe('listening')
  const now = Number(seen.annotations.at(-1).listener_ts)
  meta.listener_want = 'off'
  meta.listener_want_ts = now + 1
  await clock.advance(3_000)
  expect(seen.annotations.at(-1).listener_state).toBe('off')
  await ui.unmount()
})

test('listening ends with the meeting: the band goes back to every chat\'s Vexa band', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, () => [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...FOOTER, surface: 'desktop' })
  expect(await ui.find({ type: 'Text', text: /^[●◉]$/ })).toBeTruthy()
  endedLive.value = true
  await clock.advance(31_500)
  expect(await ui.find({ type: 'Text', text: /^[●◉]$/ })).toBeFalsy()
  // The call over, the band rests as a grey dot that opens the panel.
  expect(await ui.find({ type: 'Text', text: '○' })).toBeTruthy()
  await ui.unmount()
})

test('a trigger already heard in this call does not wake twice', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  const seen = engine(on, () => lines, 'W AWS')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  lines = [...lines, seg('2', 'Bob', 'We run it all on AWS.')]
  await clock.advance(1_500)
  lines = [...lines, seg('3', 'Ann', 'Our AWS bill doubled this year, a totally different topic.')]
  await clock.advance(60_000)
  expect(seen.triage.length).toBe(1)
  await ui.unmount()
})

test('a signal the triage model calls not worth it never reaches the main chat', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  const seen = engine(on, () => lines, 'W weather chat', {}, undefined, 'NO')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  lines = [...lines, seg('2', 'Bob', 'Lovely weather today.')]
  await clock.advance(1_500)
  expect(seen.triage.length).toBe(1)
  expect(seen.prompts.filter(p => p.includes('signal'))).toEqual([])
  await ui.unmount()
})

test('listener_configure: plain rules, triggers and presets; it cannot start listening', async ($, on) => {
  mock.clock(on)
  engine(on, () => [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const r: any = { result: JSON.parse(((await $.tool.call({ tool: 'mcp__vexa-live__listener_configure', rules: 'Wake on any price.', triggers: [{ kind: 'keyword', value: 'discount' }] } as any)) as any).result) }
  expect(r.result.listening).toBe(false)
  expect(r.result.brief.focus).toBe('Wake on any price.')
  expect(r.result.brief.rules[0].value).toBe('discount')
})

test('first run seeds ~/.vexa/apps/<name>; an update keeps edited files and lands the new version beside them', async ($, on) => {
  mock.clock(on)
  const root = 'PLUGIN'
  const manifest = (v: string) => JSON.stringify({ schema: 'vexa-app/v1', name: 'meeting-assistant', version: v, listener: { defaultPreset: 'default' } })
  const files: Record<string, string> = {
    [`${root}/app/vexa-app.json`]: manifest('0.3.0'),
    [`${root}/app/pane.json`]: '{"title":"Vexa v3"}',
    [`${root}/app/listener/default.json`]: '{"focus":"new default rules"}',
    '/home/u/.vexa/apps/meeting-assistant/vexa-app.json': manifest('0.2.0'),
    '/home/u/.vexa/apps/meeting-assistant/pane.json': '{"title":"Vexa"}',
    '/home/u/.vexa/apps/meeting-assistant/listener/default.json': '{"focus":"my own rules"}',
    '/home/u/.vexa/apps/meeting-assistant/.upstream/vexa-app.json': manifest('0.2.0'),
    '/home/u/.vexa/apps/meeting-assistant/.upstream/pane.json': '{"title":"Vexa"}',
    '/home/u/.vexa/apps/meeting-assistant/.upstream/listener/default.json': '{"focus":"old default rules"}',
  }
  const seen = engine(on, () => [], undefined, files)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const dir = '/home/u/.vexa/apps/meeting-assistant'
  expect(seen.written[`${dir}/pane.json`]).toBe('{"title":"Vexa v3"}')
  expect(seen.written[`${dir}/listener/default.json`]).toBeUndefined()
  expect(seen.written[`${dir}/listener/default.json.upstream`]).toBe('{"focus":"new default rules"}')
  expect(seen.written[`${dir}/.upstream/vexa-app.json`]).toBe(manifest('0.3.0'))
  const r: any = { result: JSON.parse(((await $.tool.call({ tool: 'mcp__vexa-live__listener_configure' } as any)) as any).result) }
  expect(r.result.appFolder).toBe(dir)
  expect(r.result.brief.focus).toBe('my own rules')
})

test('a clean profile: the shipped app is copied to ~/.vexa/apps/<name> with its .upstream base', async ($, on) => {
  mock.clock(on)
  const root = 'PLUGIN'
  const seen = engine(on, () => [], undefined, {
    [`${root}/app/vexa-app.json`]: JSON.stringify({ schema: 'vexa-app/v1', name: 'meeting-assistant', version: '0.2.0' }),
    [`${root}/app/listener/default.json`]: '{"focus":"shipped"}',
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  expect(Object.keys(seen.written).sort()).toEqual([
    '/home/u/.vexa/apps/meeting-assistant/.upstream/listener/default.json',
    '/home/u/.vexa/apps/meeting-assistant/.upstream/vexa-app.json',
    '/home/u/.vexa/apps/meeting-assistant/listener/default.json',
    '/home/u/.vexa/apps/meeting-assistant/vexa-app.json',
  ])
})

test('pure parts: brief bounds, triggers, the filter reply, direct address, app folder, calibration score', () => {
  const b = normalize({ labels: [{ name: 'price' }, { name: 'Bad Name' }], wake_at: 500, rules: [{ kind: 'regex', value: '(a+)+' }, { kind: 'person', value: 'ann' }] })
  expect(b.labels.map(l => l.name)).toEqual(['addressed_to_vexa', 'price'])
  expect(b.wake_at).toBe(1)
  expect(b.rules).toEqual([{ kind: 'person', value: 'ann', wake: true }])
  expect(ruleHit(b.rules, [{ speaker: 'Ann Lee', text: 'hi' }])).toMatch(/person/)
  expect(rulesText(briefFor(FALLBACK_APP, 'default'))).toMatch(/addresses the assistant/)
  expect(rulesText(b)).toMatch(/money terms/)
  expect(parseDecision(' W').wake).toBe(true)
  expect(parseDecision('W price €40k for the pilot')).toEqual({ wake: true, reason: 'price €40k for' })
  expect(parseDecision('S').wake).toBe(false)
  expect(triggerWords('rule keyword "AWS"')).toBe('AWS')
  expect(isDirect(['Bob: Claude, what did we agree?'])).toBe(true)
  expect(isDirect(['Bob: Клод, прочитай'])).toBe(true)
  expect(isDirect(['Bob: the cloud is fine'])).toBe(false)
  const m = parseManifest(JSON.stringify({ schema: 'vexa-app/v1', name: 'acme-sales', upstream: { repo: 'https://github.com/Vexa-ai/vexa', path: 'clients/claude-plugin/app', ref: 'main' }, listener: { defaultPreset: 'sales' } }))
  const a = buildApp('.vexa/app', m!, '{"title":"Acme","demo":false}', { 'sales.json': '{"focus":"money"}', 'broken.json': '{' })
  expect(Object.keys(a.presets)).toEqual(['sales'])
  expect(a.pane).toEqual({ title: 'Acme', emptyHint: expect.any(String), demo: false, offerListening: true })
  expect(a.upstream).toBe('https://github.com/Vexa-ai/vexa/clients/claude-plugin/app@main')
  const s = calibrationSummary([
    { text: 'a', expected: true, got: true, reason: '', ms: 10 }, { text: 'b', expected: false, got: true, reason: '', ms: 20 },
    { text: 'c', expected: true, got: false, reason: '', ms: 30 },
  ])
  expect(s).toMatchObject({ tp: 1, fp: 1, fn: 1, precision: 0.5, recall: 0.5, ms_p50: 20 })
})


test('only the chat Vexa talks to speaks: another, more recently used chat on the same machine means this one stays silent', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  // Another chat was used more recently (and no panel is open anywhere): it is the one Vexa talks to.
  const seen = engine(on, () => lines, 'W annual plan decision', { '/home/u/.vexa/link/used/s-other-chat': '2' })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  lines = [...lines, seg('2', 'Ann', 'We decided to go with the annual plan.')]
  await clock.advance(200_000)
  expect(seen.prompts).toEqual([])
})
