import { expect, mock, test } from 'claude-code/testing'

import { FALLBACK_APP, briefFor, buildApp, calibrationSummary, isDirect, normalize, parseDecision, parseManifest, ruleHit, rulesText, triggerWords } from '../hooks/listener'

const BAND = {
  plugin: 'vexa',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })
const seg = (id: string, speaker: string, t: string, completed = true) => ({ segment_id: id, speaker, text: t, completed, absolute_start_time: '2026-10-24T12:00:00Z' })

type Seen = { tools: string[]; model: string[]; prompts: string[]; written: Record<string, string>; annotations: any[]; maps: string[]; upserts: any[]; logs: string[] }
const meta: Record<string, unknown> = {}

function engine(on: any, segments: () => unknown[], decision = 'S', fs: Record<string, string> = {}, live?: (() => unknown[]) | 'strict'): Seen {
  const seen: Seen = { tools: [], model: [], prompts: [], written: {}, annotations: [], maps: [], upserts: [], logs: [] }
  on('ui.log', (_: unknown, e: any) => { seen.logs.push(String(e.text ?? e)); return { value: undefined } })
  for (const k of Object.keys(meta)) delete meta[k]
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', () => ({ value: undefined }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('env.get', (_: unknown, e: any) => ({ value: e.name === 'HOME' ? '/home/u' : undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: ['calendar_status', 'workspace_list', 'start_demo_meeting', 'get_meeting_transcript'].map(n => ({ name: `mcp__plugin_vexa_vexa__${n}` })) }))
  on('mcp.call', (_: unknown, e: any) => {
    seen.tools.push(e.tool)
    if (e.tool === 'list_meetings') return { value: text({ meetings: [{ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active', data: { title: 'Acme pricing call' } }] }) }
    if (e.tool === 'get_meeting_transcript' && e.args?.include_pending && live === 'strict') {
      return { value: { isError: true, content: [{ type: 'text', text: "Input validation error: Additional properties are not allowed ('include_pending', 'live_wait_ms' were unexpected)" }] } }
    }
    if (e.tool === 'get_meeting_transcript' && e.args?.include_pending && live) {
      return { value: text({ status: 'active', segments: [], live: { events: live(), cursor: 'c', ended: false } }) }
    }
    if (e.tool === 'get_meeting_transcript') {
      const all = segments()
      const from = Number(e.args?.since_index ?? 0)
      return { value: text({ status: 'active', since_index: from, next_index: all.length, segments: all.slice(from) }) }
    }
    if (e.tool === 'entity_upsert') { seen.upserts.push(e.args); return { value: text({ path: e.args.kind === 'meeting' ? 'kg/entities/meeting/x.md' : `kg/entities/${e.args.kind}/y.md` }) } }
    if (e.tool === 'annotate_meeting') { Object.assign(meta, e.args.metadata); seen.annotations.push({ ...e.args.metadata }); return { value: text({ ok: true }) } }
    if (e.tool === 'get_meeting') return { value: text({ id: 41, data: { metadata: { ...meta } } }) }
    if (e.tool === 'request_meeting_bot') return { value: text({ id: 41, platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active' }) }
    return { value: text({}) }
  })
  on('model.complete', (_: unknown, e: any) => {
    if (JSON.stringify(e.system ?? '').includes('knowledge map')) {
      seen.maps.push(String(e.prompt))
      return { value: { isAnswered: true, text: JSON.stringify({ entities: [
        { kind: 'person', name: 'Ann Lee', facts: ['Ann leads procurement at [[Acme]].'], fields: { role: 'Procurement lead', company: 'Acme' }, connections: [{ name: 'Acme', relation: 'works at' }] },
        { kind: 'company', name: 'Acme', facts: ['Acme is renewing for a year.'], fields: {}, connections: [] },
        { kind: 'weird', name: 'X', facts: [] },
      ], decided: ['Annual plan at 38k'], committed: ['Bob: contract by Friday'], numbers: ['38k'], open_questions: ['Who is Zed?'] }), usage: { inputTokens: 1, outputTokens: 1 } } }
    }
    seen.model.push(String(e.prompt))
    return { value: { isAnswered: true, text: decision, usage: { inputTokens: 1, outputTokens: 1 } } }
  })
  on('prompt.submit', (_: unknown, e: any) => { seen.prompts.push(e.text); return { text: e.text } })
  // The plugin's own folder is spelled PLUGIN here: the kit sets the real root.
  const key = (p: string) => (p.startsWith('/home/') || p.includes('.vexa/app') ? p : `PLUGIN${p.slice(p.lastIndexOf('/app'))}`)
  const files = { ...fs }
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

test('a live meeting is offered; nothing is listened to before Listen', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [seg('1', 'Ann', 'Our budget is 40k')])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /^Not listening$/ }))?.text).toBe('Not listening')
  expect(await ui.find({ type: 'Text', text: /Claude/ })).toBeFalsy()
  expect(seen.tools).not.toContain('get_meeting_transcript')
  expect(seen.model.length).toBe(0)
  await ui.press({ key: 'decline' })
  await clock.advance(64_000)
  expect(seen.model.length).toBe(0)
  await ui.unmount()
})

test('after Listen, a new line the filter flags wakes the main Claude once; the gap holds the next', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  const seen = engine(on, () => lines, 'W annual plan decision')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(31_500)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await ui.press({ key: 'listen' })
  expect(seen.prompts.at(-1)).toMatch(/pressed Listen/)
  expect(seen.model.length).toBe(0)
  lines = [...lines, seg('2', 'Ann', 'We decided to go with the annual plan.')]
  await clock.advance(1_500)
  expect(seen.model.at(-1)).toMatch(/annual plan/)
  expect(seen.model.at(-1)).not.toMatch(/NEW:\nAnn: Morning/)
  expect(seen.prompts.filter(p => p.includes('annual plan decision')).length).toBe(1)
  const signal = seen.prompts.find(p => p.includes('annual plan decision'))!
  expect(signal).toMatch(/^\[Vexa listener · signal\] annual plan decision — google_meet abc-defg-hij/)
  expect(signal).toMatch(/since_index=1/)
  expect(signal).not.toMatch(/We decided to go with the annual plan/)
  expect((await ui.find({ type: 'Text', text: /annual plan decision/ }))?.text).toBe('annual plan decision')
  expect(await ui.find({ type: 'Text', text: /Claude|Heard/ })).toBeFalsy()
  expect(await ui.find({ type: 'Text', text: /woke|filter|Mode/ })).toBeFalsy()
  lines = [...lines, seg('3', 'Bob', 'And we decided on a 3 year term.')]
  await clock.advance(3_000)
  expect(seen.prompts.filter(p => p.includes('annual plan decision')).length).toBe(1)
  await ui.unmount()
})

test('a draft is replaced in place by its final version, and a withdrawn draft disappears', async ($, on) => {
  const clock = mock.clock(on)
  let lines: unknown[] = []
  engine(on, () => lines)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'vexa', component: 'Pane', requestId: 'vexa-home', surface: 'desktop', props: { title: 'Vexa', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } } as any)
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  lines = [seg('a', 'Ann', 'we decid', false), seg('b', 'Bob', 'uh', false)]
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /we decid/ }))?.text).toBe('we decid')
  lines = [seg('a', 'Ann', 'We decided.')]
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /We decided\./ }))?.text).toBe('We decided.')
  expect(await ui.find({ type: 'Text', text: /^uh$/ })).toBeFalsy()
  await ui.unmount()
})

test('on a deployment with the live feed, drafts come from it and a retract removes them', async ($, on) => {
  const clock = mock.clock(on)
  let batches: unknown[][] = []
  engine(on, () => [], undefined, {}, () => batches.shift() ?? [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'vexa', component: 'Pane', requestId: 'vexa-home', surface: 'desktop', props: { title: 'Vexa', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } } as any)
  batches = [
    [{ type: 'transcript', id: 'a', speaker: 'Ann', text: 'we deci', completed: false }, { type: 'transcript', id: 'b', speaker: 'Bob', text: 'hm', completed: false }],
    [{ type: 'transcript', id: 'a', speaker: 'Ann', text: 'We decided.', completed: true }, { type: 'retract', segment_ids: ['b'] }],
  ]
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /we deci/ }))?.text).toBe('we deci')
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /We decided\./ }))?.text).toBe('We decided.')
  expect(await ui.find({ type: 'Text', text: /^hm$/ })).toBeFalsy()
  await ui.unmount()
})

test('a server whose transcript tool refuses the live-feed arguments falls back to confirmed reads', async ($, on) => {
  const clock = mock.clock(on)
  let lines: unknown[] = []
  engine(on, () => lines, undefined, {}, 'strict')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'vexa', component: 'Pane', requestId: 'vexa-home', surface: 'desktop', props: { title: 'Vexa', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } } as any)
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  lines = [seg('a', 'Maya', 'Hi everyone!')]
  await clock.advance(1_500)
  await clock.advance(1_500)
  expect((await ui.find({ type: 'Text', text: /Hi everyone/ }))?.text).toBe('Hi everyone!')
  expect(await ui.find({ type: 'Text', text: /Additional properties/ })).toBeFalsy()
  await ui.unmount()
})

test('web and plugin share the listener through meeting metadata: state, heartbeat, the web\'s Stop', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [seg('1', 'Ann', 'Morning all.')])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'vexa', component: 'Pane', requestId: 'vexa-home', surface: 'desktop', props: { title: 'Vexa', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } } as any)
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  await ui.press({ key: 'listen' })
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
  expect(await ui.find({ type: 'Text', text: /Claude is not listening/ })).toBeTruthy()
  await ui.unmount()
})

test('while listening, the live map upserts people and companies into personal and keeps one meeting page', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [seg('1', 'Ann', 'Morning all.')]
  const seen = engine(on, () => lines)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const ui = await $.ui.mount({ plugin: 'vexa', component: 'Pane', requestId: 'vexa-home', surface: 'desktop', props: { title: 'Vexa', isFocused: false, bodyColumns: 60, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} } } as any)
  await ui.input({ key: 'h-url', text: 'https://meet.google.com/abc-defg-hij' } as any)
  await clock.advance(1_500)
  await ui.press({ key: 'listen' })
  lines = [...lines, seg('2', 'Ann', 'I lead procurement at Acme.'), seg('3', 'Bob', 'We agreed the annual plan at 38k.'), seg('4', 'Bob', 'I will send the contract by Friday.')]
  await clock.advance(121_500)
  expect(seen.maps.length).toBe(1)
  expect(seen.maps[0]).toMatch(/procurement at Acme/)
  const kinds = seen.upserts.map(u => `${u.kind}:${u.name}`)
  expect(kinds).toEqual(expect.arrayContaining(['person:Ann Lee', 'company:Acme']))
  expect(kinds.some(k => k.startsWith('weird'))).toBe(false)
  expect(seen.upserts.every(u => u.slug === 'personal' && /meeting 41/.test(u.source))).toBe(true)
  const page = seen.upserts.find(u => u.kind === 'meeting')
  expect(page.fields).toMatchObject({ participants: 'Ann Lee', decided: 'Annual plan at 38k', committed: 'Bob: contract by Friday' })
  expect(page.open_questions).toEqual(['Who is Zed?'])
  expect(seen.tools).toContain('meeting_attach_page')
  expect(seen.prompts.some(p => p.includes('Updated your map'))).toBe(false)
  await ui.unmount()
})

test('listener_configure: plain rules, triggers and presets; it cannot start listening', async ($, on) => {
  mock.clock(on)
  engine(on, () => [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const r: any = await $.tool.call({ tool: 'mcp__vexa__listener_configure', rules: 'Wake on any price.', triggers: [{ kind: 'keyword', value: 'discount' }] } as any)
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
  const r: any = await $.tool.call({ tool: 'mcp__vexa__listener_configure' } as any)
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
