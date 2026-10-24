// Vexa meeting pane: opens beside the chat at session start and drives the plugin's own `vexa`
// MCP server. Not signed in → a «Sign in» state; signed in → paste a link or try the demo, then
// the live transcript follows (polled with since_index) until the bot stops.
//
// Permissions: the pane's own calls and Vexa's read-only tools run without a prompt. Anything the
// model calls that acts in a meeting (speak_in_meeting, stop_bot, request_meeting_bot, …) keeps
// the session's normal prompt.
//
// Listener: when a meeting of the person's is live, a band above the prompt offers to listen.
// Only after they press Listen, each new stretch of that meeting's transcript goes to a small
// model with the listen brief; when it says the moment matters, the main Claude gets a turn with
// the excerpt and why. The main Claude tunes the brief with the `listener_configure` tool.
import type { Register } from 'claude-code'

import type { AppInfo, Line, Listener, Meeting, Room, Seg } from '../types'
import {
  CONTEXT_LINES, FALLBACK_APP, SYSTEM, batchScore, briefFor, buildApp, normalize, parseManifest,
  parseScores, ruleHit, scoringPrompt, topLabels, wakeReason,
} from './listener'
import type { App } from './listener'

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

function info(a: App): AppInfo {
  return { name: a.name, version: a.version, source: a.source, upstream: a.upstream, presets: Object.keys(a.presets), pane: a.pane }
}

const IDLE: Listener = {
  offer: null, declined: [], active: null, preset: FALLBACK_APP.defaultPreset,
  brief: briefFor(FALLBACK_APP, FALLBACK_APP.defaultPreset), nextIndex: 0,
  context: [], buffer: [], score: 0, since: 0, lastWakeAt: 0, wakes: [], error: '', app: info(FALLBACK_APP),
}

// The project's own app folder wins over the one the plugin ships.
const PROJECT_APP = '.vexa/app'
let app: App = FALLBACK_APP
const LISTENER = { plugin: 'vexa', key: 'listener' } as const

async function readListener($: any): Promise<Listener> {
  const { value } = await $.state.get(LISTENER)
  return value ?? IDLE
}

async function updateListener($: any, fn: (l: Listener) => Listener): Promise<void> {
  for (;;) {
    const held = await $.state.get(LISTENER)
    const r = await $.state.set(LISTENER, fn(held.value ?? IDLE), { ifVersion: held.version })
    if (r.isSet) return
  }
}

// The small model that scores each stretch, and the spacing between wakes of the main Claude:
// a rule hit or being addressed may wake after MIN_GAP_URGENT_MS, anything else after MIN_GAP_MS.
const SCORER = 'haiku'
const MIN_GAP_MS = 60_000
const MIN_GAP_URGENT_MS = 15_000
const MAX_WAKES = 30
const OFFER_EVERY_MS = 30_000
const CONFIGURE = 'listener_configure'
const CONFIGURE_TOOL = 'mcp__vexa__listener_configure'

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

async function readApp($: any, dir: string, source: string): Promise<App | null> {
  if (!(await $.fs.exists(`${dir}/vexa-app.json`))) return null
  const m = parseManifest(await $.fs.read(`${dir}/vexa-app.json`))
  if (!m) return null
  const paneFile = `${dir}/${m.entry?.pane ?? 'pane.json'}`
  const paneText = (await $.fs.exists(paneFile)) ? await $.fs.read(paneFile) : null
  const listenerDir = `${dir}/${m.entry?.listener ?? 'listener'}`
  const briefs: Record<string, string> = {}
  if (await $.fs.exists(listenerDir)) {
    for (const f of await $.fs.list(listenerDir)) {
      if (f.kind === 'file' && f.name.endsWith('.json')) briefs[f.name] = await $.fs.read(`${listenerDir}/${f.name}`)
    }
  }
  return buildApp(source, m, paneText, briefs)
}

async function loadApp($: any): Promise<void> {
  try {
    app = (await readApp($, PROJECT_APP, PROJECT_APP)) ?? (await readApp($, `${$.plugin.root}/app`, 'plugin')) ?? FALLBACK_APP
  } catch { app = FALLBACK_APP }
  await updateListener($, x => (x.active ? { ...x, app: info(app) } : {
    ...x, app: info(app), preset: app.defaultPreset, brief: briefFor(app, app.defaultPreset),
  }))
}

// Copies the shipped app into the project so the team can change it and keep it in git.
async function copyTree($: any, from: string, to: string): Promise<number> {
  let n = 0
  for (const f of await $.fs.list(from)) {
    if (f.kind === 'dir') n += await copyTree($, `${from}/${f.name}`, `${to}/${f.name}`)
    else if (f.kind === 'file') { await $.fs.write(`${to}/${f.name}`, await $.fs.read(`${from}/${f.name}`)); n += 1 }
  }
  return n
}

async function seedApp($: any): Promise<string> {
  if (await $.fs.exists(`${PROJECT_APP}/vexa-app.json`)) return `${PROJECT_APP} already holds an app; nothing copied.`
  const n = await copyTree($, `${$.plugin.root}/app`, PROJECT_APP)
  await loadApp($)
  return `Copied the default Vexa app (${n} files) to ${PROJECT_APP}. Edit its listener presets and pane.json, then commit it.`
}

const roomKey = (r: Room) => `${r.platform}/${r.nativeId}`

// A live meeting of the person's, not yet offered or declined, becomes the band's offer.
let lastOfferCheck = 0
async function findLive($: any): Promise<void> {
  const l = await readListener($)
  const now = await $.clock.now()
  if (!l.app.pane.offerListening || l.active || l.offer || now - lastOfferCheck < OFFER_EVERY_MS) return
  lastOfferCheck = now
  try {
    const p = await call($, 'list_meetings', { status: 'active', limit: 5 })
    const rows: any[] = Array.isArray(p) ? p : p.meetings ?? p.items ?? []
    const live = rows.map(r => ({
      platform: String(r.platform ?? ''),
      nativeId: String(r.native_meeting_id ?? r.native_id ?? ''),
      title: String(r.data?.title ?? r.title ?? r.native_meeting_id ?? 'Your meeting'),
    })).find(r => r.platform && r.nativeId && !l.declined.includes(roomKey(r)))
    if (live) await updateListener($, x => (x.active || x.offer ? x : { ...x, offer: live }))
  } catch { /* offering is best effort; the next check tries again */ }
}

async function listen($: any): Promise<void> {
  const l = await readListener($)
  if (!l.offer) return
  const room = l.offer
  await updateListener($, x => ({ ...IDLE, declined: x.declined, active: room, preset: x.preset, brief: x.brief }))
  void $.prompt.submit({
    text: `[Vexa listener] The person pressed Listen on "${room.title}" (${room.platform} ${room.nativeId}). `
      + `I follow its transcript and wake you when something matters under the current listen brief `
      + `(preset ${l.preset}). If you can tell what kind of conversation this is, tune what I listen for `
      + `with ${CONFIGURE}; otherwise just acknowledge in one line.`,
  })
}

async function decline($: any): Promise<void> {
  await updateListener($, x => (x.offer ? { ...x, declined: [...x.declined, roomKey(x.offer)].slice(-50), offer: null } : x))
}

async function stopListening($: any): Promise<void> {
  await updateListener($, x => (x.active ? { ...IDLE, declined: [...x.declined, roomKey(x.active)].slice(-50), preset: x.preset, brief: x.brief } : x))
}

// One step: read what is new, check the rule triggers, score the rest with the small model, and
// wake the main Claude when the running score, being addressed, or the wait says so.
let scoring = false
async function listenStep($: any): Promise<void> {
  const l = await readListener($)
  if (!l.active || scoring) return
  scoring = true
  try {
    const p = await call($, 'get_meeting_transcript', { platform: l.active.platform, native_meeting_id: l.active.nativeId, since_index: l.nextIndex })
    const segs: any[] = p.segments ?? p.transcripts ?? []
    const fresh: Line[] = segs.map(s => ({ speaker: String(s.speaker ?? s.speaker_name ?? '—'), text: String(s.text ?? '') }))
      .filter(x => x.text.trim())
    const next = typeof p.next_index === 'number' ? p.next_index : l.nextIndex + segs.length
    const status = String(p.status ?? p.meeting_status ?? '')
    const now = await $.clock.now()
    let reason = fresh.length ? ruleHit(l.brief.rules, fresh) : null
    let scores: Record<string, number> = {}
    if (fresh.length && !reason) {
      const r = await $.model.complete({ model: SCORER, system: SYSTEM, prompt: scoringPrompt(l.brief, l.context, fresh), maxTokens: 300, effort: 'low' })
      if (r.isAnswered) scores = parseScores(r.text, l.brief)
    }
    const running = l.score + batchScore(scores, l.brief)
    const buffer = [...l.buffer, ...fresh].slice(-200)
    const since = l.since || (fresh.length ? now : 0)
    reason = reason ?? wakeReason({ scores, running, brief: l.brief, buffered: buffer.length, since, now })
    if (reason && ['completed', 'failed', 'stopped'].includes(status) && !buffer.length) reason = null
    const urgent = reason === 'addressed' || (reason ?? '').startsWith('rule')
    const gapOk = !l.lastWakeAt || now - l.lastWakeAt >= (urgent ? MIN_GAP_URGENT_MS : MIN_GAP_MS)
    const wake = reason !== null && gapOk && l.wakes.length < MAX_WAKES
    const context = [...l.context, ...fresh].slice(-CONTEXT_LINES)
    if (!wake) {
      await updateListener($, x => ({ ...x, nextIndex: next, context, buffer, score: running, since, error: '' }))
    } else {
      const why = reason === 'threshold' ? `scores: ${topLabels(scores) || 'accumulated'}` : reason!
      const excerpt = buffer.slice(-12).map(x => `${x.speaker}: ${x.text}`).join('\n')
      const at = new Date(now).toISOString().slice(11, 19)
      await updateListener($, x => ({
        ...x, nextIndex: next, context, buffer: [], score: 0, since: 0, lastWakeAt: now, error: '',
        wakes: [...x.wakes, { at, reason: why, excerpt: excerpt.slice(0, 400) }].slice(-10),
      }))
      void $.prompt.submit({
        text: `[Vexa listener] "${l.active.title}" (${l.active.platform} ${l.active.nativeId}): woke because ${why}.\n`
          + `New speech since the last wake:\n${excerpt}\n\n`
          + `Decide whether the person needs anything from you right now; if not, say nothing more than a line. `
          + `Adjust what I listen for with ${CONFIGURE}.`,
      })
    }
    if (['completed', 'failed', 'stopped'].includes(status)) await stopListening($)
  } catch (err) {
    await updateListener($, x => ({ ...x, error: message(err) }))
  } finally {
    scoring = false
  }
}

// The main Claude's control: read the listener, change its brief (a preset per kind of
// conversation, labels, focus, known, rule triggers, wake_at, max_wait_s), or stop it. Starting
// is the person's alone: the tool can never begin listening to a meeting.
async function configure($: any, input: any): Promise<unknown> {
  const l = await readListener($)
  const action = String(input.action ?? 'status')
  if (action === 'stop') {
    await stopListening($)
    return { listening: false }
  }
  if (action === 'update') {
    const preset = typeof input.preset === 'string' && app.presets[input.preset] ? input.preset : l.preset
    const base = preset !== l.preset ? briefFor(app, preset) : l.brief
    const brief = normalize({
      labels: input.labels ?? base.labels, focus: input.focus ?? base.focus, known: input.known ?? base.known,
      wake_at: input.wake_at ?? base.wake_at, max_wait_s: input.max_wait_s ?? base.max_wait_s, rules: input.rules ?? base.rules,
    })
    await updateListener($, x => ({ ...x, preset, brief }))
    return { listening: Boolean(l.active), meeting: l.active, preset, brief }
  }
  return { listening: Boolean(l.active), meeting: l.active, offer: l.offer, preset: l.preset, presets: l.app.presets, app: l.app.name, brief: l.brief, wakes: l.wakes }
}

const CONFIGURE_SCHEMA = {
  type: 'object',
  properties: {
    action: { enum: ['status', 'update', 'stop'], description: 'status (default) reads the listener; update changes the brief; stop stops listening.' },
    preset: { type: 'string', description: "A starting brief per kind of conversation, from the app's listener presets (status lists them; the default app has default, sales, standup, interview). Labels and focus you pass override it." },
    labels: {
      type: 'array', maxItems: 11,
      items: { type: 'object', required: ['name'], properties: {
        name: { type: 'string', pattern: '^[a-z][a-z0-9_]{0,39}$' },
        question: { type: 'string', maxLength: 2000, description: 'Asked of each new stretch of speech; well-known names (decision, action_item, question_to_user, price, objection, next_step, blocker, red_flag) have one.' },
        wake: { type: 'boolean', description: 'Whether this label counts toward waking you (default true).' },
      } },
      description: 'What to listen for; replaces the list. addressed_to_vexa is always kept.',
    },
    focus: { type: 'string', maxLength: 2000, description: 'What matters in this conversation, in a sentence or two.' },
    known: { type: 'string', maxLength: 8000, description: 'What you already know; repeats of it score near 0 (use it for what to ignore).' },
    rules: {
      type: 'array', maxItems: 20,
      items: { type: 'object', required: ['kind', 'value'], properties: {
        kind: { enum: ['keyword', 'person', 'regex'] }, value: { type: 'string', minLength: 1, maxLength: 200 }, wake: { type: 'boolean' },
      } },
      description: 'Triggers that wake you at once with no model call: a keyword, a speaker name, or a regex.',
    },
    wake_at: { type: 'number', minimum: 0.05, maximum: 100, description: 'Running score that wakes you (default 1.0: about one clear hit).' },
    max_wait_s: { type: 'number', minimum: 10, maximum: 3600, description: 'Wake anyway this long after the first unreported line (default 3600).' },
  },
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'vexa', description: 'Open the Vexa meeting pane beside the chat' })
    await $.command.register({ name: 'vexa-app-seed', description: 'Copy the default Vexa app (pane copy, listener presets) into .vexa/app to change it' })
    await loadApp($)
    void $.ui.open({ id: PANE, title: 'Vexa' })
    void connect($)
    await $.tool.register({
      name: CONFIGURE,
      description: 'Control the Vexa meeting listener: read it, set what it listens for in the live meeting the '
        + 'person chose to have listened to (a preset such as sales, standup or interview; labels; focus; what is already '
        + 'known; keyword/person/regex triggers; how readily it wakes you), or stop it. It cannot start listening: only the '
        + "person's Listen press does that.",
      inputSchema: CONFIGURE_SCHEMA,
    })
    if (!polling) {
      polling = true
      $.clock.every(4000, async () => { await poll($); await listenStep($); await findLive($) })
    }
    return next(e)
  })

  on('command.run', { command: 'vexa' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Vexa' })
    await connect($)
    return { text: 'Vexa pane opened.' }
  })

  on('command.run', { command: 'vexa-app-seed' }, async $ => ({ text: await seedApp($) }))

  on('tool.call', { tool: 'mcp__vexa__listener_configure' }, async ($, e) => ({ result: await configure($, e) }))
    .catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener control failed; try again.' }))

  // The pane's own calls are the person's button presses: approved. A model call is approved
  // only for a read-only tool; the rest goes to the session's decider as it would without us.
  on('tool.check', async ($, e, next) => {
    if (e.tool === CONFIGURE_TOOL) return { decision: 'allow', reason: "Vexa listener's own control" }
    if (!e.tool.startsWith(prefix)) return next(e)
    if (next.origin.plugin === $.plugin.name) return { decision: 'allow', reason: 'Vexa pane button' }
    if (READ_ONLY.has(e.tool.slice(prefix.length))) return { decision: 'allow', reason: 'Vexa read-only tool' }
    return next(e)
  })
    .catch(($, e, next) => next(e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const l = await readListener($)
    if (!l.offer || l.active || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    const room = l.offer
    return (
      <Box flexDirection="row" gap={1}>
        <Text>{`Vexa: "${room.title}" is live. Listen and tell you when something matters?`}</Text>
        <Button key="listen" label="Listen" variant="primary" onPress={() => listen($)} />
        <Button key="decline" label="No" onPress={() => decline($)} />
      </Box>
    )
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
    const l = await readListener($)
    const room = Math.max(4, (e.viewport?.rows ?? 30) - (l.active ? 20 : 12))
    return (
      <Box flexDirection="column">
        <Text bold>{l.app.pane.title}</Text>
        {l.active ? (
          <Box key="listener" flexDirection="column">
            <Text>{`Listening: ${l.active.title} · ${l.preset} (app ${l.app.name}, ${l.app.source})`}</Text>
            <Text dimColor>{`Watching for: ${l.brief.labels.filter(x => x.wake).map(x => x.name).join(', ')}`}</Text>
            {l.brief.focus ? <Text dimColor>{`Focus: ${l.brief.focus}`}</Text> : null}
            {l.brief.rules.length ? <Text dimColor>{`Triggers: ${l.brief.rules.map(r => `${r.kind} "${r.value}"`).join(', ')}`}</Text> : null}
            {l.wakes.slice(-3).map((w, i) => <Text key={`wake-${i}`} dimColor>{`${w.at} woke Claude: ${w.reason}`}</Text>)}
            {l.error ? <Text color="red">{l.error}</Text> : null}
            <Button key="unlisten" label="Stop listening" onPress={() => stopListening($)} />
          </Box>
        ) : null}
        {m.nativeId ? <Text dimColor>{`${m.isDemo ? 'demo · ' : ''}${m.platform} ${m.nativeId} · ${m.status}`}</Text> : null}
        {!live ? <Input key="url" label="Meeting link" placeholder="https://meet.google.com/abc-defg-hij" value={m.url} submitLabel="Send bot" onInput={(v: string) => update($, x => ({ ...x, url: v.trim() }))} onSubmit={(v: string) => send($, v)} /> : null}
        <Box flexDirection="row" gap={1}>
          {!live ? <Button key="send" label="Send bot" variant="primary" onPress={() => send($, m.url)} /> : null}
          {!live && l.app.pane.demo ? <Button key="demo" label="Try a demo" onPress={() => start($, 'start_demo_meeting', {}, true)} /> : null}
          {live ? <Button key="stop" label="Stop bot" onPress={() => stop($)} /> : null}
          {!live && m.segments.length ? <Button key="clear" label="Clear" onPress={() => update($, x => ({ ...EMPTY, connection: x.connection }))} /> : null}
        </Box>
        {m.error ? <Text color="red">{m.error}</Text> : null}
        {m.segments.length === 0 ? (
          <Text dimColor>{live
            ? (m.isDemo ? 'The demo presenter starts speaking in a few seconds…' : 'Waiting for the bot to be admitted and for speech…')
            : l.app.pane.emptyHint}</Text>
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
