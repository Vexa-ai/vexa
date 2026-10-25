// One-chat install: the plugin pairs, receives the person's key once they sign in, and serves Vexa's
// own tools in the same chat by forwarding them over HTTP.
import { expect, mock, test } from 'claude-code/testing'
import { PAIR_CODE, forwardable, resultText, rpcMessage } from '../hooks/pairing'

test('pure parts: the code, a JSON or SSE answer, the result text, the tools forwarded', () => {
  expect('Install Vexa (code VX-7K2M-Q9PD): …'.match(PAIR_CODE)?.[0]).toBe('VX-7K2M-Q9PD')
  expect('VX-0O1I-AAAA'.match(PAIR_CODE)).toBeNull()
  expect(rpcMessage('{"jsonrpc":"2.0","id":1,"result":{"ok":1}}').result).toEqual({ ok: 1 })
  expect(rpcMessage('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n').result).toEqual({ tools: [] })
  expect(resultText({ content: [{ type: 'text', text: 'a' }, { type: 'text', text: 'b' }] })).toBe('a\nb')
  expect(forwardable([{ name: 'whats_waiting', description: 'q' }, { name: 'vexa_setup' }], ['vexa_setup']).map(t => t.name)).toEqual(['whats_waiting'])
})

test('setup: pair, the person signs in, Vexa\'s tools appear in this chat and the chat is told', async ($, on) => {
  const clock = mock.clock(on)
  mock.store(on)
  const registered: string[] = []
  const prompts: string[] = []
  let bound = false
  on('tool.register', (_: unknown, e: any) => { registered.push(e.name); return { value: { tool: `mcp__vexa-live__${e.name}` } } })
  on('command.register', () => ({ value: { command: 'vexa' } }))
  on('session.start', (_: unknown, e: any) => ({ cwd: e.cwd }))
  on('session.id', () => ({ value: 's-setup' }))
  on('fs.exists', () => ({ value: false }))
  on('env.get', () => ({ value: undefined }))
  on('tool.list', () => ({ value: [] }))
  on('prompt.submit', (_: unknown, e: any) => { prompts.push(e.text); return { text: e.text } })
  on('http.fetch', (_: unknown, e: any) => {
    const url = String(e.url)
    if (url.endsWith('/pair') && e.init?.method === 'POST') return { value: { status: 201, ok: true, headers: {}, text: JSON.stringify({ code: 'VX-7K2M-Q9PD', expires_at: 0 }) } }
    if (url.includes('/pair/VX-7K2M-Q9PD')) return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify(bound ? { bound: true, email: 'ana@acme.io', key: 'vxa_k' } : { bound: false }) } }
    if (url.endsWith('/mcp')) {
      const body = JSON.parse(e.init.body)
      if (body.method === 'initialize') return { value: { status: 200, ok: true, headers: { 'mcp-session-id': 'm1' }, text: JSON.stringify({ jsonrpc: '2.0', id: 1, result: {} }) } }
      if (body.method === 'tools/list') return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ jsonrpc: '2.0', id: 2, result: { tools: [{ name: 'whats_waiting', description: 'queue' }, { name: 'request_meeting_bot', description: 'bot' }] } }) } }
      if (body.method === 'tools/call') return { value: { status: 200, ok: true, headers: {}, text: JSON.stringify({ jsonrpc: '2.0', id: 3, result: { content: [{ type: 'text', text: `called ${body.params.name}` }] } }) } }
      return { value: { status: 202, ok: true, headers: {}, text: '' } }
    }
    return { value: { status: 404, ok: false, headers: {}, text: '' } }
  })
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  const started = JSON.parse(String((await $.tool.call({ tool: 'mcp__vexa-live__vexa_setup' } as any)).result))
  expect(started.code).toBe('VX-7K2M-Q9PD')
  expect(started.next).toMatch(/open "https:\/\/claude-rc-app\.dev\.vexa\.ai\/signin\?code=VX-7K2M-Q9PD"/)
  await clock.advance(2100)
  expect(registered).not.toContain('whats_waiting')
  bound = true
  await clock.advance(2100)
  expect(registered).toEqual(expect.arrayContaining(['whats_waiting', 'request_meeting_bot']))
  expect(prompts.at(-1)).toMatch(/^\[Vexa\] Signed in as ana@acme\.io — Vexa is connected in this chat \(2 tools\)/)
  const called = await $.tool.call({ tool: 'mcp__vexa-live__whats_waiting' } as any)
  expect(called.result).toBe('called whats_waiting')
})
