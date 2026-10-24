// Vexa meeting pane: opens beside the chat at session start and drives the plugin's own `vexa`
// MCP server. Not signed in → a «Sign in» state; signed in → paste a link or try the demo, then
// the live transcript follows (polled with since_index) until the bot stops.
//
// Permissions: the pane's own calls and Vexa's read-only tools run without a prompt. Anything the
// model calls that acts in a meeting (speak_in_meeting, stop_bot, request_meeting_bot, …) keeps
// the session's normal prompt.
import type { Register } from 'claude-code'

import type { Meeting, Seg } from '../types'

const PANE = 'vexa-meeting'
const SERVER_KEY = 'vexa'
const EMPTY: Meeting = {
  connection: 'connecting', url: '', platform: '', nativeId: '', isDemo: false,
  status: 'idle', nextIndex: 0, segments: [], error: '',
}
const MEETING = { plugin: 'vexa', key: 'meeting' } as const

async function read($: any): Promise<Meeting> {
  const { value } = await $.state.get(MEETING)
  return value ?? EMPTY
}

// Read, apply, write at the version read; again on a miss, so two presses before a redraw both land.
async function update($: any, fn: (m: Meeting) => Meeting): Promise<void> {
  for (;;) {
    const held = await $.state.get(MEETING)
    const r = await $.state.set(MEETING, fn(held.value ?? EMPTY), { ifVersion: held.version })
    if (r.isSet) return
  }
}

// Tools that only read. A model call to any of them is approved; everything else asks.
export const READ_ONLY = new Set([
  'whats_waiting', 'parse_meeting_link', 'list_meetings', 'get_meeting', 'get_meeting_transcript',
  'get_meeting_participants', 'get_meeting_chat', 'search_transcripts', 'list_recordings',
  'get_recording', 'get_bot_status', 'timeline', 'reactions_list', 'flows_list', 'friction_so_far',
  'bot_accounts_list', 'open_meeting',
])

// The name `$.mcp.call` takes, once `connect` answered; the module's own, so a reload reconnects.
let server = ''
let polling = false

// `plugin:vexa:vexa` → `mcp__plugin_vexa_vexa__`, the prefix the model's calls carry.
export function toolPrefix(name: string): string {
  return `mcp__${name.replace(/[^A-Za-z0-9_-]/g, '_')}__`
}

let prefix = toolPrefix(`plugin:vexa:${SERVER_KEY}`)

// MCP results come back as content blocks; Vexa returns one JSON text block.
function payload(r: { content: readonly { type: string; text?: string }[] }): any {
  const t = r.content.find(c => c.type === 'text')?.text ?? ''
  try { return JSON.parse(t) } catch { return { raw: t } }
}

function message(err: unknown): string {
  return String((err as Error)?.message ?? err).slice(0, 300)
}

async function connect($: any): Promise<void> {
  const r = await $.mcp.connect(SERVER_KEY)
  if (r.isConnected) {
    server = r.server
    prefix = toolPrefix(r.server)
    await update($, x => ({ ...x, connection: 'ready', error: '' }))
    return
  }
  server = ''
  await update($, x => ({
    ...x,
    connection: r.reason === 'auth' ? 'signin' : 'refused',
    error: r.reason === 'auth' ? '' : r.message,
  }))
}

async function call($: any, tool: string, args: Record<string, unknown>): Promise<any> {
  if (!server) await connect($)
  if (!server) throw new Error('Vexa is not connected yet.')
  const r = await $.mcp.call(server, tool, args)
  const p = payload(r)
  if (r.isError) throw new Error(typeof p.raw === 'string' ? p.raw : JSON.stringify(p).slice(0, 200))
  return p
}

function isLive(m: Meeting): boolean {
  return m.nativeId !== '' && !['idle', 'stopped', 'completed', 'failed'].includes(m.status)
}

async function poll($: any): Promise<void> {
  const m = await read($)
  if (m.connection !== 'ready' || !isLive(m)) return
  try {
    const p = await call($, 'get_meeting_transcript', { platform: m.platform, native_meeting_id: m.nativeId, since_index: m.nextIndex })
    const segs: any[] = p.segments ?? p.transcripts ?? []
    const fresh: Seg[] = segs.map(s => ({
      speaker: String(s.speaker ?? s.speaker_name ?? '—'),
      text: String(s.text ?? ''),
      time: String(s.absolute_start_time ?? s.start_time ?? '').slice(11, 19),
    })).filter(s => s.text.trim())
    const next = typeof p.next_index === 'number' ? p.next_index : m.nextIndex + segs.length
    const status = String(p.status ?? p.meeting_status ?? m.status)
    await update($, x => ({ ...x, nextIndex: next, status, segments: [...x.segments, ...fresh].slice(-300), error: '' }))
  } catch (err) {
    await update($, x => ({ ...x, error: message(err) }))
  }
}

async function start($: any, tool: string, args: Record<string, unknown>, isDemo: boolean): Promise<void> {
  await update($, x => ({ ...x, isDemo, status: 'requesting', error: '', segments: [], nextIndex: 0, nativeId: '' }))
  try {
    const bot = await call($, tool, args)
    await update($, x => ({
      ...x,
      platform: String(bot.platform ?? ''),
      nativeId: String(bot.native_meeting_id ?? ''),
      status: String(bot.status ?? 'requested'),
    }))
  } catch (err) {
    await update($, x => ({ ...x, status: 'idle', error: message(err) }))
  }
}

async function send($: any, url: string): Promise<void> {
  const link = url.trim()
  await update($, x => ({ ...x, url: link }))
  if (link) await start($, 'request_meeting_bot', { meeting_url: link }, false)
}

async function stop($: any): Promise<void> {
  const m = await read($)
  try { await call($, 'stop_bot', { platform: m.platform, native_meeting_id: m.nativeId }) } catch (err) {
    await update($, x => ({ ...x, error: message(err) }))
  }
  await update($, x => ({ ...x, status: 'stopped' }))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'vexa', description: 'Open the Vexa meeting pane beside the chat' })
    void $.ui.open({ id: PANE, title: 'Vexa' })
    void connect($)
    if (!polling) { polling = true; $.clock.every(4000, () => poll($)) }
    return next(e)
  })

  on('command.run', { command: 'vexa' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Vexa' })
    await connect($)
    return { text: 'Vexa pane opened.' }
  })

  // The pane's own calls are the person's button presses: approved. A model call is approved
  // only for a read-only tool; the rest goes to the session's decider as it would without us.
  on('tool.check', async ($, e, next) => {
    if (!e.tool.startsWith(prefix)) return next(e)
    if (next.origin.plugin === $.plugin.name) return { decision: 'allow', reason: 'Vexa pane button' }
    if (READ_ONLY.has(e.tool.slice(prefix.length))) return { decision: 'allow', reason: 'Vexa read-only tool' }
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input } = $.ui.resolve(e) as any
    const m = await read($)

    if (m.connection === 'connecting') {
      return <Box flexDirection="column"><Text bold>Vexa</Text><Text dimColor>Connecting…</Text></Box>
    }

    if (m.connection !== 'ready') {
      return (
        <Box flexDirection="column">
          <Text bold>Vexa</Text>
          {m.connection === 'signin' ? (
            <Box flexDirection="column">
              <Text>Sign in to Vexa to send a bot to your meetings.</Text>
              <Text>{'Run /mcp, pick plugin:vexa:vexa, then Authenticate. Sign in with Google or Microsoft and press Allow.'}</Text>
              <Text dimColor>New accounts start with $2 of free credit.</Text>
            </Box>
          ) : (
            <Text color="red">{m.error || 'Vexa is not connected.'}</Text>
          )}
          <Button key="retry" label="I've signed in" variant="primary" onPress={() => connect($)} />
        </Box>
      )
    }

    const live = isLive(m) || m.status === 'requesting'
    const room = Math.max(4, (e.viewport?.rows ?? 30) - 12)
    return (
      <Box flexDirection="column">
        <Text bold>Vexa</Text>
        {m.nativeId ? <Text dimColor>{`${m.isDemo ? 'demo · ' : ''}${m.platform} ${m.nativeId} · ${m.status}`}</Text> : null}
        {!live ? <Input key="url" label="Meeting link" placeholder="https://meet.google.com/abc-defg-hij" value={m.url} submitLabel="Send bot" onInput={(v: string) => update($, x => ({ ...x, url: v.trim() }))} onSubmit={(v: string) => send($, v)} /> : null}
        <Box flexDirection="row" gap={1}>
          {!live ? <Button key="send" label="Send bot" variant="primary" onPress={() => send($, m.url)} /> : null}
          {!live ? <Button key="demo" label="Try a demo" onPress={() => start($, 'start_demo_meeting', {}, true)} /> : null}
          {live ? <Button key="stop" label="Stop bot" onPress={() => stop($)} /> : null}
          {!live && m.segments.length ? <Button key="clear" label="Clear" onPress={() => update($, x => ({ ...EMPTY, connection: x.connection }))} /> : null}
        </Box>
        {m.error ? <Text color="red">{m.error}</Text> : null}
        {m.segments.length === 0 ? (
          <Text dimColor>{live
            ? (m.isDemo ? 'The demo presenter starts speaking in a few seconds…' : 'Waiting for the bot to be admitted and for speech…')
            : 'No meeting right now? Try a demo: a presenter speaks for a minute and you watch it transcribed live.'}</Text>
        ) : null}
        {m.segments.slice(-room).map((s, i) => (
          <Box key={`seg-${i}`} flexDirection="row">
            <Text bold>{`${s.time ? s.time + ' ' : ''}${s.speaker}: `}</Text>
            <Text>{s.text}</Text>
          </Box>
        ))}
      </Box>
    )
  })
}
