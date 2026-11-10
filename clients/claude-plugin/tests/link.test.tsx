import { expect, mock, test } from 'claude-code/testing'

import { EVENT_MS, LINK_STALE_MS, TIMED_OUT, mayRead, within } from '../hooks/register'

// THE LINE FROM VEXA KEEPS READING (10 Nov 2026, card brain:card/8e490542f481): a chat whose read of the line hung read
// nothing more for an hour, panel presses included. A read that hangs is left behind; a press is acked once the chat
// took it, and said once however often Vexa hands it over.

const HOME = '/home/u'
const LINK = `${HOME}/.vexa/link`
const press = (id: string) => JSON.stringify({ type: 'ui', request: { id, action: 'notes_lines', label: 'Notes lines', text: 'These are lines of my own Notes on page:x: ^aaaaaa.', params: { object: 'page:x', lines: ['aaaaaa'], chat: 's-test' }, created_ms: 1 } })

function engine(on: any, spool: string) {
  const prompts: string[] = []
  const files: Record<string, string> = {
    [`${LINK}/used/s-test`]: '1',
    [`${LINK}/events.jsonl`]: spool,
    [`${LINK}/offset`]: '0',
  }
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('tool.register', (_: unknown, e: any) => ({ value: { tool: `mcp__vexa__${e.name}` } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.toast', () => ({ value: undefined }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 's-test' }))
  on('env.get', (_: unknown, e: any) => ({ value: e.name === 'HOME' ? HOME : undefined }))
  on('mcp.connect', () => ({ value: { isConnected: true, server: 'plugin:vexa:vexa' } }))
  on('tool.list', () => ({ value: [] }))
  on('mcp.call', () => ({ value: { content: [{ type: 'text', text: '{}' }] } }))
  on('prompt.submit', (_: unknown, e: any) => { prompts.push(String(e.text)); return { text: e.text } })
  const dirOf = (p: string) => Object.keys(files).filter(f => f.startsWith(`${p}/`))
  on('fs.exists', (_: unknown, e: any) => ({ value: e.path in files || dirOf(e.path).length > 0 }))
  on('fs.read', (_: unknown, e: any) => (e.path in files ? { value: files[e.path] } : { deny: `ENOENT ${e.path}` }))
  on('fs.write', (_: unknown, e: any) => { files[e.path] = e.text; return { value: undefined } })
  on('fs.list', (_: unknown, e: any) => {
    const names = new Map<string, 'file' | 'dir'>()
    for (const f of dirOf(e.path)) {
      const [head, ...tail] = f.slice(e.path.length + 1).split('/')
      names.set(head!, tail.length ? 'dir' : 'file')
    }
    return { value: [...names].map(([name, kind]) => ({ name, kind, size: 0, mtimeMs: 0, isLink: false })) }
  })
  return { prompts, files, running: (now: number) => { files[`${LINK}/state.json`] = JSON.stringify({ state: 'running', ts: now, pid: 1 }) } }
}

test('within: the answer when it comes first, TIMED_OUT when the time passes first', async () => {
  const fake = { clock: { sleep: (ms: number) => new Promise<void>(r => setTimeout(r, ms)) } }
  expect(await within(fake, Promise.resolve(7), 20)).toBe(7)
  expect(await within(fake, new Promise(() => undefined), 20)).toBe(TIMED_OUT)
  expect(EVENT_MS).toBeLessThan(LINK_STALE_MS)
})

test('a read that hangs is left behind after LINK_STALE_MS; one running for less is waited for', () => {
  expect(mayRead(false, 0, 5)).toBe(true)
  expect(mayRead(true, 1_000, 1_000 + LINK_STALE_MS - 1)).toBe(false)
  expect(mayRead(true, 1_000, 1_000 + LINK_STALE_MS)).toBe(true)
})

test('a press Vexa hands over twice is said once; it is acked only once the chat took it', async ($, on) => {
  const clock = mock.clock(on)
  const t = engine(on, `${press('cd34')}\n${press('cd34')}\n`)
  t.running(0)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (let s = 0; s < 4; s++) { t.running(s * 1_000); await clock.advance(1_000) }
  expect(t.prompts.filter(p => p.includes('«Notes lines»'))).toHaveLength(1)
  expect(t.files[`${LINK}/acks/cd34`]).toBe('submitted')
})

test('a press whose id is not a relay id is said but never names a file', async ($, on) => {
  const clock = mock.clock(on)
  const t = engine(on, `${press('../x')}\n`)
  t.running(0)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (let s = 0; s < 3; s++) { t.running(s * 1_000); await clock.advance(1_000) }
  expect(t.prompts.filter(p => p.includes('«Notes lines»'))).toHaveLength(1)
  expect(Object.keys(t.files).filter(f => f.includes('/acks/') || f.includes('/said/'))).toEqual([])
})
