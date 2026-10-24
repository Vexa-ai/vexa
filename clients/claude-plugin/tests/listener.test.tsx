import { expect, mock, test } from 'claude-code/testing'

import { defaultBrief, normalize, ruleHit, wakeReason } from '../hooks/listener'

const BAND = {
  plugin: 'vexa',
  component: 'AbovePrompt',
  props: { hasSurvey: false, isWorking: false, maxRows: 10, bodyColumns: 80, scroll: { offset: 0, bodyRows: 10 }, view: {} },
} as const

const text = (o: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(o) }] })

type Seen = { tools: string[]; model: string[]; prompts: string[] }

function engine(on: any, segments: () => unknown[], scores = '{}'): Seen {
  const seen: Seen = { tools: [], model: [], prompts: [] }
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', () => ({ value: { tool: 'mcp__vexa__listener_configure' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('mcp.call', (_: unknown, e: any) => {
    seen.tools.push(e.tool)
    if (e.tool === 'list_meetings') return { value: text({ meetings: [{ platform: 'google_meet', native_meeting_id: 'abc-defg-hij', status: 'active', data: { title: 'Acme pricing call' } }] }) }
    if (e.tool === 'get_meeting_transcript') {
      const all = segments()
      const from = Number(e.args?.since_index ?? 0)
      return { value: text({ status: 'active', next_index: all.length, segments: all.slice(from) }) }
    }
    return { value: text({}) }
  })
  on('model.complete', (_: unknown, e: any) => {
    seen.model.push(String(e.prompt))
    return { value: { isAnswered: true, text: scores, usage: { inputTokens: 1, outputTokens: 1 } } }
  })
  on('prompt.submit', (_: unknown, e: any) => { seen.prompts.push(e.text); return { text: e.text } })
  return seen
}

test('a live meeting is offered; nothing is listened to before Listen', async ($, on) => {
  const clock = mock.clock(on)
  const seen = engine(on, () => [{ speaker: 'Ann', text: 'Our budget is 40k' }])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(32_000)
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  expect((await ui.find({ type: 'Text', text: /Acme pricing call/ }))?.text).toMatch(/is live/)
  expect(seen.tools).not.toContain('get_meeting_transcript')
  expect(seen.model.length).toBe(0)
  await ui.press({ key: 'decline' })
  await clock.advance(64_000)
  expect(seen.model.length).toBe(0)
  await ui.unmount()
})

test('after Listen, a scored stretch wakes the main Claude once, then waits out the gap', async ($, on) => {
  const clock = mock.clock(on)
  let lines = [{ speaker: 'Ann', text: 'We decided to go with the annual plan.' }]
  const seen = engine(on, () => lines, '{"addressed_to_vexa":0,"decision":0.95,"action_item":0.1,"question_to_user":0}')
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  await clock.advance(32_000)
  const ui = await $.ui.mount({ ...BAND, surface: 'terminal' })
  await ui.press({ key: 'listen' })
  expect(seen.prompts.at(-1)).toMatch(/pressed Listen/)
  await clock.advance(4_000)
  expect(seen.model.length).toBeGreaterThan(0)
  expect(seen.model[0]).toMatch(/annual plan/)
  const wakes = seen.prompts.filter(p => p.includes('woke because'))
  expect(wakes.length).toBe(1)
  expect(wakes[0]).toMatch(/decision/)
  lines = [...lines, { speaker: 'Bob', text: 'And we decided on a 3 year term.' }]
  await clock.advance(8_000)
  expect(seen.prompts.filter(p => p.includes('woke because')).length).toBe(1)
  await ui.unmount()
})

test('listener_configure sets a preset and triggers but cannot start listening', async ($, on) => {
  mock.clock(on)
  engine(on, () => [])
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const r: any = await $.tool.call({ tool: 'mcp__vexa__listener_configure', action: 'update', preset: 'sales', rules: [{ kind: 'keyword', value: 'discount' }] } as any)
  expect(r.result.listening).toBe(false)
  expect(r.result.preset).toBe('sales')
  expect(r.result.brief.labels.map((l: any) => l.name)).toContain('price')
  expect(r.result.brief.rules[0].value).toBe('discount')
})

test('brief rules: addressed_to_vexa is kept, bounds hold, unsafe regex refused, triggers match', () => {
  const b = normalize({ labels: [{ name: 'price' }, { name: 'Bad Name' }], wake_at: 500, rules: [{ kind: 'regex', value: '(a+)+' }, { kind: 'person', value: 'ann' }] })
  expect(b.labels.map(l => l.name)).toEqual(['addressed_to_vexa', 'price'])
  expect(b.wake_at).toBe(1)
  expect(b.rules).toEqual([{ kind: 'person', value: 'ann', wake: true }])
  expect(ruleHit(b.rules, [{ speaker: 'Ann Lee', text: 'hi' }])).toMatch(/person/)
  const d = defaultBrief()
  expect(wakeReason({ scores: { addressed_to_vexa: 0.8 }, running: 0, brief: d, buffered: 1, since: 1, now: 2 })).toBe('addressed')
  expect(wakeReason({ scores: {}, running: 0.3, brief: d, buffered: 1, since: 1, now: 2 })).toBe(null)
})
