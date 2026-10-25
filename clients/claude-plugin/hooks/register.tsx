// Vexa in Claude: the brain, not a second UI. It signs in through the plugin's own `vexa` MCP
// server, opens our web panel in Claude's browser pane (with the person's permission), and draws
// one thing of its own: the band above the prompt.
//
// Permissions: the plugin's own calls and Vexa's read-only tools run without a prompt. Anything the
// model calls that acts in a meeting (speak_in_meeting, stop_bot, request_meeting_bot, …) keeps
// the session's normal prompt.
//
// Listener: when a meeting of the person's is live, a band above the prompt offers to listen.
// Only after they press Listen, each change in the transcript's tail goes to a small model with
// the listen brief; when it says wake, the main Claude gets a turn with the excerpt and why. The
// main Claude tunes it with `listener_configure` and measures it with `listener_calibrate`.
//
// App: listener presets come from an app folder (vexa-app.json): the project's
// `.vexa/app/`, else the person's `~/.vexa/apps/<name>/` (seeded on first run, merged with the
// plugin's newer version on update without overwriting their edits), else the plugin's own.
import type { Register } from 'claude-code'

import type { AppInfo, Flash, Listener, Meeting, Seg } from '../types'
import {
  DECIDE_MAX_TOKENS, DECIDE_SYSTEM, FALLBACK_APP, briefFor, buildApp, calibrationSummary, decidePrompt, isDirect,
  DEFAULT_MODELS, TRIAGE_SYSTEM, norm, normalize, parseDecision, parseManifest, parseTriage, ruleHit, rulesText, triggerWords,
} from './listener'
import type { App, Labelled, Models, Row } from './listener'

const EMPTY: Meeting = {
  connection: 'connecting', url: '', platform: '', nativeId: '', dbId: 0, attachedTo: '', offers: { calendar: false, workspaces: false, demo: false, requests: false, webPanel: false }, title: '', isDemo: false,
  status: 'idle', nextIndex: 0, liveCursor: '', liveFeed: true, segments: [], error: '',
}
const MEETING = { plugin: 'vexa-live', key: 'meeting' } as const

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
  offer: null, declined: [], isOn: false, preset: FALLBACK_APP.defaultPreset,
  brief: briefFor(FALLBACK_APP, FALLBACK_APP.defaultPreset), triaged: [], lastWakeAt: 0, wakes: [],
  lastVerdict: '', lastMs: 0, error: '', app: info(FALLBACK_APP),
}
const LISTENER = { plugin: 'vexa-live', key: 'listener' } as const

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

// The pane's other values, one pair of helpers each (a state reference must be a literal).
const FLASH = { plugin: 'vexa-live', key: 'flash' } as const
const NO_FLASH: Flash = { stage: 'idle', at: 0, line: '' }

async function readFlash($: any): Promise<Flash> {
  const { value } = await $.state.get(FLASH)
  return value ?? NO_FLASH
}
async function updateFlash($: any, fn: (f: Flash) => Flash): Promise<void> {
  for (;;) {
    const held = await $.state.get(FLASH)
    if ((await $.state.set(FLASH, fn(held.value ?? NO_FLASH), { ifVersion: held.version })).isSet) return
  }
}

const WAKE_GAP_MS = 45_000
const FOLLOW_MS = 25_000
const MAX_WAKES = 30
const WINDOW = 6
const OFFER_EVERY_MS = 30_000
const CONFIGURE = 'listener_configure'
const CALIBRATE = 'listener_calibrate'
const CONFIGURE_TOOL = 'mcp__vexa-live__listener_configure'
const CALIBRATE_TOOL = 'mcp__vexa-live__listener_calibrate'
const PROJECT_APP = '.vexa/app'
const BROWSER_SERVER = 'Claude_Browser'
const BROWSER_TOOL = 'mcp__Claude_Browser__preview_start'
const LIVE_WAIT_MS = 1200



// The module's own: a reload reconnects and reloads the app.
let server = ''

let polling = false
let app: App = FALLBACK_APP
let appDir = ''


let prefix = ''
// This plugin's version, from its manifest: what the server's presence records.
let pluginVersion = ''
// Whether this plugin has woken Claude yet, for the server's first-wake mark.
let firstWake = false

// MCP results come back as content blocks; Vexa returns one JSON text block, sometimes followed by
// a notice in plain text («\n\n<notice>»): parse the leading JSON value and ignore what follows.
export function leadingJson(t: string): any {
  const start = t.search(/[{[]/)
  if (start < 0) return null
  let depth = 0
  let inString = false
  for (let i = start; i < t.length; i++) {
    const c = t[i]
    if (inString) {
      if (c === '\\') i++
      else if (c === '"') inString = false
    } else if (c === '"') inString = true
    else if (c === '{' || c === '[') depth++
    else if (c === '}' || c === ']') {
      depth--
      if (depth === 0) {
        try { return JSON.parse(t.slice(start, i + 1)) } catch { return null }
      }
    }
  }
  return null
}

function payload(r: { content: readonly { type: string; text?: string }[] }): any {
  const t = r.content.find(c => c.type === 'text')?.text ?? ''
  return leadingJson(t) ?? { raw: t }
}

// Background work (timers, loads a view starts): a failure shows in the view's own error line;
// a session ending under it rejects nothing.
function quiet(p: Promise<unknown>): void {
  p.catch(() => {})
}

function message(err: unknown): string {
  return String((err as Error)?.message ?? err).slice(0, 300)
}

// The person's Vexa, however it is connected — the claude.ai connector, the Vexa plugin's server, or
// one added by hand: the server whose tools include Vexa's transcript read and its queue. This plugin
// lists no server of its own and signs in to nothing; it calls that one with the engine's own
// connection (`$.mcp.call` takes the tool-name spelling). Servers connect after the session starts,
// so the poll looks again until one is there.
export function vexaServersOf(names: readonly string[]): string[] {
  return names.filter(n => /^mcp__.+__get_meeting_transcript$/.test(n)
    && names.includes(n.replace(/get_meeting_transcript$/, 'whats_waiting')))
    .map(n => n.slice('mcp__'.length, -'__get_meeting_transcript'.length))
}

export function vexaServerOf(names: readonly string[]): string {
  return vexaServersOf(names)[0] ?? ''
}

// A server the model has called Vexa's tools on (`learnServer`): the desktop app defers MCP tools,
// so a connector's are not in `$.tool.list()` until the model loads them.
// Every Vexa server this session has (one person may have two: a connector and a hand-added one);
// the one followed is the one where their live meeting is (`findLive`).
const servers = new Set<string>()
let seen = ''

async function connect($: any): Promise<void> {
  const names: string[] = (await $.tool.list()).map((t: { name: string }) => t.name)
  for (const s of vexaServersOf(names)) servers.add(s)
  const found = seen || vexaServerOf(names)
  if (!found) {
    server = ''
    await update($, x => ({ ...x, connection: 'refused', error: '' }))
    return
  }
  const was = (await read($)).connection
  server = found
  prefix = `mcp__${found}__`
  const has = new Set(names)
  const offers = {
    calendar: has.has(`${prefix}calendar_status`),
    workspaces: has.has(`${prefix}workspace_list`),
    demo: has.has(`${prefix}start_demo_meeting`),
    requests: has.has(`${prefix}claude_requests_next`),
    // Our web panel opens in Claude's browser pane, signed in by a one-time link, when the server
    // mints those links and this Claude has the browser pane (the desktop app).
    webPanel: has.has(`${prefix}panel_link`) && has.has(BROWSER_TOOL),
  }
  await update($, x => ({ ...x, connection: 'ready', offers, error: '' }))
  if (was !== 'ready') { quiet(heartbeat($)); quiet(probeHttp($)); if (offers.requests) startRequests($) }
}

// Whether this session listens through `vexa-listen` (the script the `vexa-listen` skill starts):
// then the script is the listener, and these hooks stop polling for one of their own. On the desktop
// app it is the only one that works — there a plugin's own MCP calls are refused.
let byScript = false

// A Vexa tool the model just called names the server: `mcp__<server>__whats_waiting` and the like.
export function serverOfCall(tool: string): string {
  const m = /^mcp__(.+)__(whats_waiting|get_meeting_transcript|list_meetings|request_meeting_bot|start_demo_meeting|get_meeting)$/.exec(tool)
  return m?.[1] && m[1] !== 'vexa-live' ? m[1] : ''
}

async function learnServer($: any, tool: string): Promise<void> {
  const s = serverOfCall(tool)
  if (!s || servers.has(s)) return
  servers.add(s)
  if (!server) { seen = s; await connect($) }
}

// Whether this plugin's own HTTP reaches Vexa from here (`$.http.fetch` is not a tool call, so no
// permission check stands in front of it): the road a listen token streams over.
let httpProbe = ''
async function probeHttp($: any): Promise<void> {
  try {
    const r = await $.http.fetch('https://claude-rc.dev.vexa.ai/health')
    httpProbe = `${r.status} ${String(r.text ?? '').slice(0, 60)}`
  } catch (err) { httpProbe = `error: ${message(err)}` }
}

// The server knows this plugin is here (the presence its «Vexa Live» offer reads), and when it
// first woke Claude: `live_heartbeat`, where the server has it.
async function heartbeat($: any, event?: 'wake'): Promise<void> {
  if (!server || !(await $.tool.list()).some((t: { name: string }) => t.name === `${prefix}live_heartbeat`)) return
  await call($, 'live_heartbeat', { version: pluginVersion, ...(event ? { event } : {}) })
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
  return m.nativeId !== '' && !['idle', 'stopped', 'completed', 'failed', 'past'].includes(m.status)
}

// Without a live feed, drafts arrive again and again in the confirmed read: upsert on segment id,
// and drop a draft the latest answer no longer holds (finalized under another id, or withdrawn).
export function mergeSegs(old: readonly Seg[], fresh: readonly Seg[]): Seg[] {
  const seen = new Set(fresh.map(f => f.id))
  const out = fresh.length ? old.filter(o => !o.pending || seen.has(o.id)) : [...old]
  for (const f of fresh) {
    const i = f.id ? out.findIndex(o => o.id === f.id) : -1
    if (i >= 0) out[i] = f; else out.push(f)
  }
  return out.slice(-300)
}

// The live feed's events, as the app's live view applies them: a `transcript` event upserts its
// segment by id (a draft, `completed: false`, is revised in place), `retract` drops ids.
export function applyLive(old: readonly Seg[], events: readonly any[]): Seg[] {
  let out = [...old]
  for (const ev of events) {
    if (ev?.type === 'retract') {
      const drop = new Set<string>((ev.segment_ids ?? []).map(String))
      out = out.filter(s => !drop.has(s.id))
    } else if (ev?.type === 'transcript' && String(ev.text ?? '').trim()) {
      const seg: Seg = {
        id: String(ev.id ?? ''), speaker: String(ev.speaker ?? '—'), text: String(ev.text).trim(),
        time: clockTime(ev.tsMs), pending: ev.completed === false,
      }
      const i = seg.id ? out.findIndex(o => o.id === seg.id) : -1
      if (i >= 0) out[i] = seg; else out.push(seg)
    }
  }
  return out.slice(-300)
}

function clockTime(t: unknown): string {
  if (t === undefined || t === null || t === '') return ''
  const d = new Date(typeof t === 'number' ? t : String(t))
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', hour12: false })
}

async function readConfirmed($: any, since: number, status: string): Promise<string> {
  const m = await read($)
  const page = await call($, 'get_meeting_transcript', { platform: m.platform, native_meeting_id: m.nativeId, since_index: since })
  const segs: any[] = page.segments ?? page.transcripts ?? []
  const base = typeof page.since_index === 'number' ? page.since_index : since
  const firstPending = segs.findIndex(x => x.completed === false)
  const fresh: Seg[] = segs.map(s => ({
    id: String(s.segment_id ?? s.id ?? ''),
    speaker: String(s.speaker ?? s.speaker_name ?? '—'),
    text: String(s.text ?? '').trim(),
    time: clockTime(s.absolute_start_time ?? s.start_time),
    pending: s.completed === false,
  })).filter(s => s.text)
  // Ask again from the first draft next time, so its final version replaces it.
  const next = firstPending >= 0 ? base + firstPending : (typeof page.next_index === 'number' ? page.next_index : since + segs.length)
  const now = String(page.status ?? (status === 'past' ? 'completed' : status))
  await update($, x => ({
    ...x, liveFeed: false, nextIndex: next, status: now,
    segments: mergeSegs(x.liveFeed ? [] : x.segments, fresh), error: '',
  }))
  if (segs.length) await triage($)
  return now
}

let pollingNow = false
async function poll($: any): Promise<void> {
  const m = await read($)
  if (byScript || m.connection !== 'ready' || !(isLive(m) || m.status === 'past') || pollingNow) return
  pollingNow = true
  try {
    let status = m.status
    if (!m.liveFeed) {
      status = await readConfirmed($, m.nextIndex, status)
    } else {
      let p: any
      try {
        p = await call($, 'get_meeting_transcript', {
          platform: m.platform, native_meeting_id: m.nativeId, limit: 1, include_pending: true, live_wait_ms: LIVE_WAIT_MS,
          ...(m.liveCursor ? { live_cursor: m.liveCursor } : {}),
        })
      } catch (err) {
        // A server without the live feed refuses its arguments outright (strict tool schemas).
        if (!/include_pending|live_wait_ms|live_cursor|unexpected/i.test(message(err))) throw err
        p = {}
      }
      status = String(p.status ?? p.meeting_status ?? status)
      if (p.live) {
        const events: any[] = p.live.events ?? []
        await update($, x => ({ ...x, status, liveCursor: String(p.live.cursor ?? x.liveCursor), segments: applyLive(x.segments, events), error: '' }))
        if (events.length) await triage($)
      } else {
        // This deployment serves no live feed: confirmed lines from the start, with since_index.
        status = await readConfirmed($, 0, status)
      }
    }
    if (['completed', 'failed', 'stopped'].includes(status)) {
      await updateListener($, x => ({ ...x, isOn: false }))
    }
  } catch (err) {
    await update($, x => ({ ...x, error: message(err) }))
  } finally {
    pollingNow = false
  }
}

// --- App folder -------------------------------------------------------------------------------

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

async function files($: any, dir: string, rel = ''): Promise<string[]> {
  const out: string[] = []
  for (const f of await $.fs.list(rel ? `${dir}/${rel}` : dir)) {
    const path = rel ? `${rel}/${f.name}` : f.name
    if (f.kind === 'dir' && !f.name.startsWith('.')) out.push(...(await files($, dir, path)))
    else if (f.kind === 'file' && !f.name.endsWith('.upstream')) out.push(path)
  }
  return out
}

async function copyTree($: any, from: string, to: string): Promise<number> {
  const list = await files($, from)
  for (const f of list) await $.fs.write(`${to}/${f}`, await $.fs.read(`${from}/${f}`))
  return list.length
}

// The person's copy of the shipped app: seeded on first run; on a plugin update, a file they never
// edited takes the new version, an edited one stays and the new version lands beside it as
// `<file>.upstream`. `.upstream/` holds the shipped version their copy was last merged with.
async function userApp($: any): Promise<string> {
  const home = await $.env.get('HOME')
  const shipped = `${$.plugin.root}/app`
  if (!home || !(await $.fs.exists(`${shipped}/vexa-app.json`))) return ''
  const name = parseManifest(await $.fs.read(`${shipped}/vexa-app.json`))?.name ?? 'app'
  const dir = `${home}/.vexa/apps/${name}`
  const base = `${dir}/.upstream`
  if (!(await $.fs.exists(`${dir}/vexa-app.json`))) {
    await copyTree($, shipped, dir)
    await copyTree($, shipped, base)
    return dir
  }
  const now = await $.fs.read(`${shipped}/vexa-app.json`)
  const was = (await $.fs.exists(`${base}/vexa-app.json`)) ? await $.fs.read(`${base}/vexa-app.json`) : ''
  if (now === was) return dir
  for (const f of await files($, shipped)) {
    const next = await $.fs.read(`${shipped}/${f}`)
    const mine = (await $.fs.exists(`${dir}/${f}`)) ? await $.fs.read(`${dir}/${f}`) : null
    const old = (await $.fs.exists(`${base}/${f}`)) ? await $.fs.read(`${base}/${f}`) : null
    if (mine === null || mine === old || mine === next) await $.fs.write(`${dir}/${f}`, next)
    else if (next !== old) await $.fs.write(`${dir}/${f}.upstream`, next)
  }
  await copyTree($, shipped, base)
  return dir
}

async function loadApp($: any): Promise<void> {
  try {
    const mine = await userApp($)
    const project = await readApp($, PROJECT_APP, PROJECT_APP)
    const user = !project && mine ? await readApp($, mine, mine) : null
    const shipped = !project && !user ? await readApp($, `${$.plugin.root}/app`, 'plugin') : null
    app = project ?? user ?? shipped ?? FALLBACK_APP
    appDir = project ? PROJECT_APP : user ? mine : shipped ? `${$.plugin.root}/app` : ''
  } catch (err) {
    app = FALLBACK_APP
    appDir = ''
    await updateListener($, x => ({ ...x, error: `app folder: ${message(err)}` }))
  }
  await updateListener($, x => (x.isOn ? { ...x, app: info(app) } : {
    ...x, app: info(app), preset: app.defaultPreset, brief: briefFor(app, app.defaultPreset),
  }))
}

// Copies the person's app into the project so the team can change it and keep it in git.

// Puts the person's copy back to the plugin's version, keeping theirs as a dated backup.

// --- Listener ---------------------------------------------------------------------------------

const roomKey = (r: { platform: string; nativeId: string }) => `${r.platform}/${r.nativeId}`

// A live meeting of the person's, not yet offered or declined, becomes the band's offer.
let lastOfferCheck = 0
async function findLive($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  const now = await $.clock.now()
  if (now < lastOfferCheck) lastOfferCheck = 0
  if (byScript || !l.app.pane.offerListening || l.isOn || now - lastOfferCheck < OFFER_EVERY_MS) return
  lastOfferCheck = now
  if (m.nativeId && m.status === 'active' && !l.declined.includes(roomKey(m))) {
    await updateListener($, x => ({ ...x, offer: { platform: m.platform, nativeId: m.nativeId, title: m.title || m.nativeId, dbId: m.dbId, demo: m.isDemo } }))
    await listen($, true)
    return
  }
  if (l.offer && l.offer.platform === m.platform && l.offer.nativeId === m.nativeId && !isLive(m)) {
    await updateListener($, x => ({ ...x, offer: null }))
  }
  try {
    for (const where of [server, ...[...servers].filter(x => x && x !== server)].filter(Boolean)) {
      const r = await $.mcp.call(where, 'list_meetings', { status: 'active', limit: 5 })
      if (r?.isError) continue
      const p = payload(r)
      const rows: any[] = Array.isArray(p) ? p : p.meetings ?? p.items ?? []
      const rooms = rows.filter(x => (x.status ?? 'active') === 'active').map(x => ({
        platform: String(x.platform ?? ''),
        nativeId: String(x.native_meeting_id ?? x.native_id ?? ''),
        title: callTitle(x),
        dbId: Number(x.id ?? x.meeting_db_id ?? 0), demo: Boolean(x.data?.demo),
      }))
      // An offer whose meeting is no longer live goes; a live one stays.
      if (l.offer && rooms.some(x => roomKey(x) === roomKey(l.offer!))) return
      const live = rooms.find(x => x.platform && x.nativeId && !l.declined.includes(roomKey(x)))
      if (!live) continue
      if (where !== server) { seen = where; await connect($) }
      await updateListener($, x => (x.isOn ? x : { ...x, offer: live }))
      // Listening is on by default (founder, 25 Oct): a live meeting of theirs is followed at once;
      // Stop on the band (or the panel) is the person's override, and a stopped call stays stopped.
      await listen($, true)
      return
    }
    await updateListener($, x => (x.isOn ? x : { ...x, offer: null }))
  } catch (err) {
    // Offering is best effort and the next check tries again; the reason is kept for the status.
    await update($, x => ({ ...x, error: message(err) }))
  }
}

// Listen: the offered meeting is followed and what is already said counts as heard. A press is told
// to the main Claude, so it can tune the brief; listening that started by itself says nothing.
async function listen($: any, auto = false): Promise<void> {
  const l = await readListener($)
  const room = l.offer
  if (!room) return
  const m = await read($)
  if (m.platform !== room.platform || m.nativeId !== room.nativeId) {
    await update($, x => ({ ...x, platform: room.platform, nativeId: room.nativeId, dbId: room.dbId ?? 0, attachedTo: '', title: room.title, isDemo: Boolean(room.demo), status: 'active', segments: [], nextIndex: 0, liveCursor: '', liveFeed: true }))
    await poll($)
  }
  const heard = (await read($)).segments.filter(s => s.id).map(s => s.id)
  lastAuditAt = await $.clock.now()
  auditCount = heard.length
  await updateListener($, x => ({ ...x, offer: null, isOn: true, triaged: heard.slice(-500), wakes: [], lastWakeAt: 0, error: '' }))
  await writeState($, 'listening')
  listenedSince = await $.clock.now()
  if (auto) return
  void $.prompt.submit({
    text: `[Vexa listener] The person pressed Listen on "${room.title}" (${room.platform} ${room.nativeId}). `
      + `I watch its transcript and wake you when something matters under the "${l.preset}" listen brief. `
      + `If you can tell what kind of conversation this is, tune it with ${CONFIGURE}; otherwise acknowledge in one line.`,
  })
}

async function decline($: any): Promise<void> {
  await updateListener($, x => (x.offer ? { ...x, declined: [...x.declined, roomKey(x.offer)].slice(-50), offer: null } : x))
}

// The web's Listen for the meeting this session follows.
async function listenToMeeting($: any): Promise<void> {
  const m = await read($)
  if (!m.nativeId) return
  await updateListener($, x => ({ ...x, offer: { platform: m.platform, nativeId: m.nativeId, title: m.title || m.nativeId, dbId: m.dbId } }))
  await listen($)
}

async function setPreset($: any, preset: string): Promise<void> {
  if (!app.presets[preset]) return
  await updateListener($, x => ({ ...x, preset, brief: briefFor(app, preset) }))
  if ((await readListener($)).isOn) await writeState($, 'listening')
}

async function stopListening($: any): Promise<void> {
  const m = await read($)
  if ((await readListener($)).isOn) await writeState($, 'off')
  await updateListener($, x => ({ ...x, isOn: false, declined: m.nativeId ? [...x.declined, roomKey(m)].slice(-50) : x.declined }))
}

async function decide($: any, rules: string, context: string[], fresh: string[]): Promise<{ wake: boolean; reason: string; ms: number }> {
  const t0 = await $.clock.now()
  const r = await complete($, 'filter', {
    maxTokens: DECIDE_MAX_TOKENS,
    system: [{ text: DECIDE_SYSTEM, cache: true }, { text: `Rules: ${rules}`, cache: true }],
    prompt: decidePrompt(context, fresh),
  })
  const ms = (await $.clock.now()) - t0
  if (!r.isAnswered) return { wake: false, reason: `no answer (${r.reason})`, ms }
  return { ...parseDecision(r.text), ms }
}

// Wakes that pile up while the main Claude is busy go as ONE message, in order, once it is free.
// Spam guard: one listener message per MIN_GAP_MS at most, MAX_PER_WINDOW per WINDOW_MS (past that,
// only a direct address goes out); what is held stays in the batch and leaves as one message.
const MIN_GAP_MS = 15_000
const WINDOW_MS = 600_000
const MAX_PER_WINDOW = 8
let busy = false
let batch: { reason: string; lines: string[]; direct: boolean; index: number }[] = []

// The web and the plugin talk only through the meeting's metadata (flat keys, since
// annotate_meeting merges key-wise): the plugin writes listener_state / listener_heard /
// listener_ts (+ a heartbeat every 20 s while listening; the web treats a state older than 45 s
// as not listening) and listener_mode; the web writes listener_want ("on" | "off") and
// listener_ask ("missed") with their _ts, which the plugin follows.
const HEARTBEAT_MS = 20_000
let lastStateWrite = 0
let seenWantTs = 0
let seenAskTs = 0
async function writeState($: any, state: 'listening' | 'heard' | 'answering' | 'off', heard?: string): Promise<void> {
  const m = await read($)
  // Metadata is the bot owner's (annotate_meeting is owner-only): a teammate following a shared
  // call writes nothing there; showing a reader's state needs a per-reader slot that doesn't exist yet.
  if (!m.dbId || m.attachedTo) return
  const now = await $.clock.now()
  lastStateWrite = now
  const l = await readListener($)
  const metadata: Record<string, unknown> = { listener_state: state, listener_ts: now, listener_mode: l.preset === 'default' ? 'normal' : l.preset }
  if (heard !== undefined) metadata.listener_heard = heard.slice(-160)
  quiet(call($, 'annotate_meeting', { meeting_db_id: m.dbId, metadata }))
}

// The web's buttons: Listen / Stop listening (listener_want) and «What did I miss?» (listener_ask).
async function followWeb($: any): Promise<void> {
  const m = await read($)
  if (!m.dbId || m.attachedTo || m.connection !== 'ready' || !isLive(m)) return
  const l = await readListener($)
  const now = await $.clock.now()
  if (l.isOn && now - lastStateWrite >= HEARTBEAT_MS) await writeState($, 'listening')
  const meeting = await call($, 'get_meeting', { meeting_db_id: m.dbId })
  const md = meeting?.data?.metadata ?? meeting?.metadata ?? {}
  const wantTs = Number(md.listener_want_ts ?? 0)
  if (wantTs > seenWantTs) {
    const first = seenWantTs === 0
    seenWantTs = wantTs
    // A press made before this session saw the meeting is history, not a request.
    if (!first || now - wantTs < 60_000) {
      if (md.listener_want === 'off' && l.isOn) await stopListening($)
      if (md.listener_want === 'on' && !l.isOn) await listenToMeeting($)
    }
  }
  const mode = md.listener_mode === 'normal' ? 'default' : String(md.listener_mode ?? '')
  if (l.isOn && mode && mode !== l.preset && app.presets[mode]) await setPreset($, mode)
  const askTs = Number(md.listener_ask_ts ?? 0)
  if (askTs > seenAskTs) {
    const first = seenAskTs === 0
    seenAskTs = askTs
    if ((!first || now - askTs < 60_000) && md.listener_ask === 'missed' && l.isOn) await whatDidIMiss($)
  }
}

// What the band shows the moment the listener acts, before the chat answers: woke → queued →
// sent → answering → done, back to idle 8 s after done.
async function setFlash($: any, stage: Flash['stage'], line?: string): Promise<void> {
  const at = await $.clock.now()
  await updateFlash($, f => ({ ...f, stage, at, line: line ?? f.line }))
  if (stage === 'woke') await writeState($, 'heard', line ?? '')
  if (stage === 'answering') await writeState($, 'answering')
  if (stage === 'done') await writeState($, 'listening')
  if (stage === 'done') $.clock.after(8000, () => updateFlash($, f => (f.stage === 'done' && f.at === at ? { ...f, stage: 'idle' } : f)))
}
let sentAt: number[] = []
let flushTimer = false
async function flush($: any): Promise<void> {
  if (batch.length === 0) return
  if (busy) { await setFlash($, 'queued'); return }
  const now = await $.clock.now()
  sentAt = sentAt.filter(t => now - t < WINDOW_MS)
  const last = sentAt[sentAt.length - 1] ?? 0
  const overCap = sentAt.length >= MAX_PER_WINDOW && !batch.some(b => b.direct)
  const wait = Math.max(last ? MIN_GAP_MS - (now - last) : 0, overCap ? WINDOW_MS - (now - sentAt[0]!) : 0)
  if (wait > 0) {
    await setFlash($, 'queued')
    if (!flushTimer) { flushTimer = true; $.clock.after(wait + 50, () => { flushTimer = false; void flush($) }) }
    return
  }
  sentAt.push(now)
  const seen = new Set<string>()
  const lines: string[] = []
  for (const b of batch) for (const l of b.lines) { const k = norm(l); if (!seen.has(k)) { seen.add(k); lines.push(l) } }
  const reasons = [...new Set(batch.map(b => b.reason))].join('; ')
  const direct = batch.some(b => b.direct)
  const index = Math.min(...batch.map(b => b.index))
  batch = []
  busy = true
  await setFlash($, 'sent')
  const m = await read($)
  // Founder, 24 Oct: a wake is a signal, not an order. A direct address goes straight to the main
  // chat with the lines; any other signal first goes to the triage model in the plugin, and only a
  // yes reaches the main chat, as one line with where to read.
  if (direct) {
    void $.prompt.submit({ text: `[Vexa listener] ${reasons}\n\nFrom the live meeting "${m.title || m.nativeId}" (${m.platform} ${m.nativeId}):\n${lines.join('\n')}\n\nThis was addressed to you: answer briefly.` })
    return
  }
  let verdict = { yes: false, line: '' }
  try {
    const l = await readListener($)
    const r = await complete($, 'triage', {
      maxTokens: 60,
      system: [{ text: TRIAGE_SYSTEM, cache: true }, { text: `Listen rules: ${rulesText(l.brief)}`, cache: true }],
      prompt: `Trigger: ${reasons}\nRecent lines:\n${lines.slice(-8).join('\n')}`,
    })
    if (r.isAnswered) verdict = parseTriage(r.text)
  } catch { verdict = { yes: false, line: '' } }
  if (!verdict.yes) {
    busy = false
    await setFlash($, 'done')
    return
  }
  void $.prompt.submit({
    text: `[Vexa listener · signal] ${reasons}: ${verdict.line} — ${m.platform} ${m.nativeId} (get_meeting_transcript since_index=${index}). `
      + `Look only if it matters; otherwise let it pass with a single «·».`,
  })
}

// Judges the tail of the transcript, drafts included, whenever it changes. A rule trigger wakes
// with no model call; one wake per thought (segment id or the same words); wakes are WAKE_GAP_MS
// apart unless the speaker addressed the assistant, whose next lines within FOLLOW_MS may follow
// (the filter still has to say wake).
const WOKE_FOR = new Set<string>()
// Per call: a trigger already heard («AWS», «Ramp») doesn't wake twice; asking the assistant always does.
const SEEN_TRIGGERS = new Set<string>()
const WOKE_TEXT = new Set<string>()
let lastTail = ''
let lastDirectAt = 0
let lastTextAt = 0
let triaging = false
async function triage($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  if (!l.isOn || triaging) return
  const tail = m.segments.slice(-WINDOW)
  const tailText = tail.map(s => `${s.speaker}: ${s.text}`).join('\n')
  if (!tail.length || tailText === lastTail) return
  lastTail = tailText
  lastTextAt = await $.clock.now()
  const done = new Set(l.triaged)
  const fresh = tail.filter(s => !done.has(s.id) || s.pending)
  if (!fresh.length) return
  triaging = true
  try {
    const context = tail.filter(s => !fresh.includes(s)).map(s => `${s.speaker}: ${s.text}`)
    const lines = fresh.map(s => `${s.speaker}: ${s.text}${s.pending ? ' (still being said)' : ''}`)
    const hit = ruleHit(l.brief.rules, fresh.filter(s => !s.pending))
    const v = hit ? { wake: true, reason: hit, ms: 0 } : await decide($, rulesText(l.brief), context, lines)
    const now = await $.clock.now()
    const ids = fresh.map(s => s.id)
    const key = norm(fresh.map(s => s.text).join(' '))
    const already = ids.some(id => WOKE_FOR.has(id)) || [...WOKE_TEXT].some(t => t.length > 12 && (key.includes(t) || t.includes(key)))
    const direct = isDirect(lines)
    const followUp = lastDirectAt > 0 && now - lastDirectAt < FOLLOW_MS
    if (direct && v.wake) lastDirectAt = now
    const gapOk = !l.lastWakeAt || now - l.lastWakeAt > WAKE_GAP_MS
    const trig = `${m.platform}/${m.nativeId}|${triggerWords(v.reason).toLowerCase()}`
    const seenTrig = !direct && SEEN_TRIGGERS.has(trig)
    // Right after the speaker addressed the assistant, the main Claude is already on it: the next
    // lines don't become separate signals.
    const answering = !direct && lastDirectAt > 0 && now - lastDirectAt < FOLLOW_MS
    const canWake = !already && !seenTrig && !answering && v.wake && l.wakes.length < MAX_WAKES && (direct || followUp || Boolean(hit) || gapOk)
    if (canWake && !direct) SEEN_TRIGGERS.add(trig)
    if (canWake) { ids.forEach(id => WOKE_FOR.add(id)); WOKE_TEXT.add(key) }
    await updateListener($, x => ({
      ...x,
      triaged: [...x.triaged, ...fresh.filter(s => !s.pending).map(s => s.id)].slice(-500),
      lastVerdict: `${v.wake ? 'wake' : 'skip'} · ${v.reason}`,
      lastMs: v.ms,
      error: '',
      lastWakeAt: canWake ? now : x.lastWakeAt,
      wakes: canWake ? [...x.wakes, { at: new Date(now).toISOString().slice(11, 19), reason: v.reason }].slice(-20) : x.wakes,
    }))
    if (canWake) {
      if (!firstWake) { firstWake = true; quiet(heartbeat($, 'wake')) }
      await setFlash($, 'woke', triggerWords(v.reason))
      batch.push({ reason: v.reason, lines: [...context.slice(-2), ...lines], direct, index: Math.max(0, m.segments.length - fresh.length) })
      await flush($)
    }
  } catch (err) {
    await updateListener($, x => ({ ...x, error: message(err) }))
  } finally {
    triaging = false
  }
}

// If the listener stays quiet while the transcript keeps growing, the main Claude is asked to
// check whether the filter missed something, and to retune it if so.
const AUDIT_MS = 180_000
let lastAuditAt = 0
let auditCount = 0
async function audit($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  if (!l.isOn || !m.nativeId) return
  const now = await $.clock.now()
  const quietSince = Math.max(l.lastWakeAt, lastAuditAt)
  const grown = m.segments.length - auditCount
  if (now - quietSince < AUDIT_MS || grown < 4) return
  lastAuditAt = now
  auditCount = m.segments.length
  const tail = m.segments.slice(-12).map(s => `${s.speaker}: ${s.text}${s.pending ? ' (draft)' : ''}`).join('\n')
  void $.prompt.submit({
    text: `[Vexa listener audit] No wake for ${Math.round((now - quietSince) / 60000)} min while ${grown} new lines arrived. Check whether the filter missed something.\n\n`
      + `Last verdict: ${l.lastVerdict || 'none'} (${l.lastMs} ms)\nRules: ${rulesText(l.brief)}\n\nTranscript tail (${m.platform} ${m.nativeId}):\n${tail}\n\n`
      + `If it missed something, retune with ${CONFIGURE}; otherwise say nothing.`,
  })
}

async function whatDidIMiss($: any): Promise<void> {
  const m = await read($)
  const l = await readListener($)
  const since = l.wakes.length ? `since the last wake (${l.wakes[l.wakes.length - 1]!.at} UTC)` : 'so far'
  const tail = m.segments.slice(-40).map(s => `${s.speaker}: ${s.text}`).join('\n')
  void $.prompt.submit({ text: `[Vexa listener · «What did I miss?»] Summarize the meeting ${since} in 3–5 short bullets: decisions, asks to me, numbers, next steps. Skip personal talk.\n\n${tail}` })
}

// Replays a labelled fixture (`[{speaker, text, wake}]`) through the same filter, one line at a
// time with three lines of context, and scores it. Default fixture: the app's
// `listener/calibration/fixture.json`.
async function calibrate($: any, rules: string, fixture: string): Promise<unknown> {
  const path = fixture || (appDir ? `${appDir}/listener/calibration/fixture.json` : '')
  if (!path || !(await $.fs.exists(path))) return { error: `No fixture at ${path || '(no app folder)'}; pass fixture: <path to [{speaker,text,wake}]>.` }
  const lines: Labelled[] = JSON.parse(await $.fs.read(path))
  const rows: Row[] = []
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    const context = lines.slice(Math.max(0, i - 3), i).map(x => `${x.speaker}: ${x.text}`)
    const v = await decide($, rules, context, [`${line.speaker}: ${line.text}`])
    rows.push({ text: line.text, expected: line.wake, got: v.wake, reason: v.reason, ms: v.ms })
  }
  const summary = calibrationSummary(rows)
  const misses = rows.filter(r => r.expected !== r.got).map(r => `${r.got ? 'FALSE WAKE' : 'MISSED'}: «${r.text}»`)
  return { fixture: path, model: models.filter, summary, misses }
}

// The main Claude's control: read the listener, change its brief, or stop it. Starting is the
// person's alone: the tool can never begin listening to a meeting.
async function configure($: any, input: any): Promise<unknown> {
  const l = await readListener($)
  const m = await read($)
  const action = String(input.action ?? (input.preset || input.rules || input.focus || input.labels || input.triggers || input.known ? 'update' : 'status'))
  const meeting = l.isOn ? `${m.platform} ${m.nativeId}` : null
  if (action === 'stop') {
    await stopListening($)
    return { listening: false }
  }
  if (input.models && typeof input.models === 'object') {
    const next = { ...models }
    for (const k of ['filter', 'writer', 'triage'] as const) {
      if (typeof input.models[k] === 'string' && input.models[k].trim()) next[k] = input.models[k].trim()
    }
    models = next
    try { await $.store.set('models', models) } catch { /* kept for this session */ }
    if (action === 'status' || (!input.preset && !input.rules && !input.focus && !input.labels && !input.triggers && !input.known)) {
      return { models, spend: await spendReport($) }
    }
  }
  if (action === 'update') {
    const preset = typeof input.preset === 'string' && app.presets[input.preset] ? input.preset : l.preset
    const base = preset !== l.preset ? briefFor(app, preset) : l.brief
    // `rules` as plain text is the filter's rules (the brief's focus); as a list, triggers.
    const focus = typeof input.rules === 'string' ? input.rules : input.focus ?? base.focus
    const triggers = Array.isArray(input.rules) ? input.rules : input.triggers ?? base.rules
    const brief = normalize({
      labels: input.labels ?? base.labels, focus, known: input.known ?? base.known,
      wake_at: base.wake_at, max_wait_s: base.max_wait_s, rules: triggers,
    })
    await updateListener($, x => ({ ...x, preset, brief }))
    return { listening: l.isOn, meeting, preset, brief }
  }
  const v = await read($)
  return { vexa: { server: server || null, connection: v.connection, error: v.error || null, http: httpProbe || null }, listening: l.isOn, meeting, offer: l.offer, preset: l.preset, presets: l.app.presets, app: l.app.name, appFolder: appDir, brief: l.brief, wakes: l.wakes, lastVerdict: l.lastVerdict, spend: await spendReport($) }
}

// Web → chat: a panel button in the web («Summarize this call», «Prepare me…») queues a request
// for this person's Claude. The plugin long-polls claude_requests_next (each call is also the
// presence heartbeat the web reads as «Claude is running»), turns each request into a turn and
// acks it. The server renders the text from a fixed list of panel actions and guards the rate.
let requestsRunning = false
function startRequests($: any): void {
  if (requestsRunning) return
  requestsRunning = true
  const loop = async (): Promise<void> => {
    let pause = 0
    try {
      const m = await read($)
      if (m.connection !== 'ready' || !m.offers.requests) { requestsRunning = false; return }
      const r = await call($, 'claude_requests_next', { wait_ms: 8000 })
      for (const q of r.requests ?? []) {
        const text = String(q.text ?? '').slice(0, 2000)
        if (!text) { quiet(call($, 'claude_requests_ack', { id: q.id, outcome: 'dropped' })); continue }
        void $.prompt.submit({ text: `[Vexa panel] ${text}` })
        quiet(call($, 'claude_requests_ack', { id: q.id, outcome: 'submitted' }))
      }
    } catch { pause = 10_000 }
    $.clock.after(pause || 250, () => quiet(loop()))
  }
  quiet(loop())
}

// --- Models per job, and what each job spends ---------------------------------------------------
// filter (wake or skip + trigger), writer (the live map), triage (is a signal worth the person's
// attention): each runs in the plugin through Claude Code's model access, so the main chat spends
// nothing on them. Changed from chat with listener_configure; kept across sessions.
let models: Models = DEFAULT_MODELS
type Spend = { calls: number; input: number; output: number }
const spend: Record<keyof Models, Spend> = { filter: { calls: 0, input: 0, output: 0 }, writer: { calls: 0, input: 0, output: 0 }, triage: { calls: 0, input: 0, output: 0 } }
let listenedSince = 0

async function complete($: any, job: keyof Models, request: Record<string, unknown>): Promise<any> {
  const r = await $.model.complete({ model: models[job], ...request })
  const u: any = r.usage ?? {}
  spend[job].calls += 1
  spend[job].input += Number(u.inputTokens ?? u.input_tokens ?? 0) + Number(u.cacheReadInputTokens ?? 0) + Number(u.cacheCreationInputTokens ?? 0)
  spend[job].output += Number(u.outputTokens ?? u.output_tokens ?? 0)
  return r
}

async function spendReport($: any): Promise<Record<string, unknown>> {
  const hours = listenedSince ? Math.max(1 / 60, ((await $.clock.now()) - listenedSince) / 3_600_000) : 0
  const per = (x: Spend) => ({ ...x, tokens_per_hour: hours ? Math.round((x.input + x.output) / hours) : null })
  return { models, listened_minutes: Math.round(hours * 60), filter: per(spend.filter), writer: per(spend.writer), triage: per(spend.triage) }
}


const TRIGGERS_SCHEMA = {
  type: 'array', maxItems: 20,
  items: { type: 'object', required: ['kind', 'value'], properties: {
    kind: { enum: ['keyword', 'person', 'regex'] }, value: { type: 'string', minLength: 1, maxLength: 200 }, wake: { type: 'boolean' },
  } },
}

const CONFIGURE_SCHEMA = {
  type: 'object',
  properties: {
    action: { enum: ['status', 'update', 'stop'], description: 'status reads the listener; update changes what it listens for; stop stops listening. Inferred from the other fields when absent.' },
    preset: { type: 'string', description: "Start from one of the app's presets (status lists them; the default app has default, sales, standup, interview)." },
    rules: { description: 'Plain-language rules: what should wake you and what to ignore, tuned to this conversation (a string). A list of {kind: keyword|person|regex, value} instead sets instant triggers.' },
    triggers: { ...TRIGGERS_SCHEMA, description: 'Instant triggers that wake you with no model call: a keyword, a speaker name, or a regex.' },
    known: { type: 'string', maxLength: 8000, description: 'What you already know or do not want to hear about again.' },
    labels: { type: 'array', items: { type: 'object', required: ['name'], properties: { name: { type: 'string' }, question: { type: 'string' }, wake: { type: 'boolean' } } }, description: 'Extra things to wake on, each a named question (signals.v1 labels).' },
    models: {
      type: 'object',
      description: 'Which model each listener job uses (haiku, sonnet, opus or a full id): filter = wake or skip, writer = the live map into the personal workspace, triage = whether a signal deserves the person now. «Use haiku for notes» = {writer: "haiku"}. Status shows them and the tokens each job spent per listened hour.',
      properties: { filter: { type: 'string' }, writer: { type: 'string' }, triage: { type: 'string' } },
    },
  },
}

// --- Calls, calendar, workspaces: what the views beyond the meeting read --------------------

function callTitle(x: any): string {
  return String(x.data?.title || (x.data?.demo ? 'Demo meeting' : x.platform === 'google_meet' ? 'Google Meet call' : x.platform === 'teams' ? 'Teams call'
    : x.platform === 'zoom' ? 'Zoom call' : x.platform === 'jitsi' ? 'Jitsi call' : x.platform === 'vexa_call' ? 'Vexa call' : 'Call'))
}

// --- Workspaces: list them, open one at its README and its .md pages, bind the open meeting --

// --- The one Vexa pane: views switch inside it (the engine cannot bring an open tab to the front) --


// Colours (terminal design guidelines 2026-10-10 §1–2, founder 24 Oct): monochrome theme keys,
// emphasis from weight, never red, sparing with blue; green means live and nothing else.

// The panel origin the plugin is about to open (from panel_link's answer): the plugin's own
// preview_start call is approved for that origin only.
let panelOrigin = ''
// Origins the person said Yes to once: Claude Code's dialog for a hook's ask offers no «always»,
// so the plugin keeps that answer itself ($.store, across sessions).
let panelAllowed: string[] = []
export function originOf(url: unknown): string {
  try { return new URL(String(url)).origin } catch { return '' }
}

// The one app: our web panel in Claude's browser pane. panel_link answers a single-use sign-in
// link (≤120 s) for this account landing on `next`; it is opened at once and never kept, shown or
// logged. The plugin draws no UI of its own besides the band: without a browser pane, nothing opens.
async function openPanel($: any, next: string): Promise<boolean> {
  const m = await read($)
  if (m.connection === 'ready' && m.offers.webPanel) {
    try {
      const r = await call($, 'panel_link', { next })
      if (r?.url) return await openInPane($, String(r.url))
    } catch { /* not opened: the caller says so in one plain line, or nothing */ }
  }
  return false
}

async function openInPane($: any, url: string): Promise<boolean> {
  try {
    panelOrigin = originOf(url)
    const opened = await $.mcp.call(BROWSER_SERVER, 'preview_start', { url })
    if (opened?.isError) return false
    if (!panelAllowed.includes(panelOrigin)) {
      panelAllowed = [...panelAllowed, panelOrigin]
      try { await $.store.set('panelAllowed', panelAllowed) } catch { /* asked again next session */ }
    }
    return true
  } catch { return false }
}

function panelPath(m: Meeting): string {
  return m.nativeId ? `/panel/meeting/${encodeURIComponent(m.platform)}/${encodeURIComponent(m.nativeId)}` : '/panel'
}

const LIVE = 'success'

function pulse(ui: any, key: string, isOn: boolean): any {
  const { Svg, Text } = ui
  if (!Svg) return <Text key={key} color={isOn ? LIVE : 'inactive'}>●</Text>
  const anim = isOn ? '<animate attributeName="r" values="3;5;3" dur="1.4s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;0.45;1" dur="1.4s" repeatCount="indefinite"/>' : ''
  return <Svg key={key} alt={isOn ? 'live' : 'off'} width={14} height={14} isInteractive={isOn ? true : undefined} source={`<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="4" fill="${isOn ? '#2f9e72' : '#9AA0A6'}">${anim}</circle></svg>`} />
}

export const register: Register = (on) => {
  on('session.start', async ($, e, next) => {
    try { pluginVersion = String(JSON.parse(await $.fs.read(`${$.plugin.root}/.claude-plugin/plugin.json`))?.version ?? '') } catch { pluginVersion = '' }
    await $.command.register({ name: 'vexa', description: 'Open the Vexa call panel beside the chat' })
    await $.tool.register({
      name: CONFIGURE,
      description: 'Control the Vexa meeting listener: read it, set what it listens for in the live meeting the person chose '
        + 'to have listened to (plain-language rules, a preset such as sales, standup or interview, instant keyword/person/regex '
        + 'triggers, what is already known), or stop it. It cannot start listening: only the person\'s Listen press does that.',
      inputSchema: CONFIGURE_SCHEMA,
    })
    await $.tool.register({
      name: CALIBRATE,
      description: "Replay a labelled transcript fixture through the listener's small-model filter with the given rules (or the current "
        + 'ones) and return precision, recall, per-call latency and the misses. No meeting needed.',
      inputSchema: { type: 'object', properties: { rules: { type: 'string' }, fixture: { type: 'string', description: 'Path to a JSON list of {speaker, text, wake}.' } } },
    })
    try {
      const savedPanel = await $.store.get('panelAllowed')
      if (Array.isArray(savedPanel)) panelAllowed = savedPanel.filter((o): o is string => typeof o === 'string')
      const savedModels = await $.store.get('models')
      if (savedModels && typeof savedModels === 'object') models = { ...DEFAULT_MODELS, ...(savedModels as Models) }
    } catch { /* defaults */ }
    await loadApp($)
    await connect($)
    if (!polling) {
      polling = true
      $.clock.every(1500, () => quiet((async () => { await poll($); await findLive($) })()))
      $.clock.every(3000, () => quiet(followWeb($)))
      $.clock.every(300_000, () => quiet(heartbeat($)))
      // Signing in happens in the browser, outside the pane: notice it without a button press.
      $.clock.every(5000, async () => {
        try { if ((await read($)).connection !== 'ready') await connect($) } catch { /* the next tick tries again */ }
      })
      $.clock.every(30_000, () => quiet(audit($)))
    }
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    busy = true
    if ((await readFlash($)).stage === 'sent') await setFlash($, 'answering')
    return next(e)
  })
  on('turn.complete', async ($, e, next) => {
    const r = await next(e)
    busy = false
    if ((await readFlash($)).stage === 'answering') await setFlash($, 'done')
    await flush($)
    return r
  })

  on('command.run', { command: 'vexa' }, async $ => {
    await connect($)
    const m = await read($)
    if (m.connection !== 'ready') return { text: "Connect Vexa first: ask Claude to set up Vexa." }
    if (!m.offers.webPanel) return { text: "Vexa's call panel opens beside the chat in the Claude desktop app." }
    return { text: (await openPanel($, panelPath(m))) ? 'Vexa opened.' : "Vexa's panel didn't open." }
  })

  // A registered tool answers text: the engine checks a hook's result against a string.
  // A call Claude starts from the chat (a bot or the demo) is followed by the pane, as the pane's
  // own buttons do: «Claude opens the call beside the chat».
  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (!ran.deny && !ran.isError) await learnServer($, e.tool).catch(() => {})
    if (e.tool === 'Bash' && /vexa-listen/.test(String((e as { command?: unknown }).command ?? ''))) byScript = true
    const name = prefix && e.tool.startsWith(prefix) ? e.tool.slice(prefix.length) : ''
    if ((name === 'request_meeting_bot' || name === 'start_demo_meeting') && !ran.deny && !ran.isError) try {
      const bot = leadingJson(String(ran.text ?? ''))
      const m = await read($)
      if (bot?.native_meeting_id && !isLive(m)) {
        await update($, x => ({
          ...x, platform: String(bot.platform ?? ''), nativeId: String(bot.native_meeting_id), dbId: Number(bot.id ?? 0),
          isDemo: name === 'start_demo_meeting' || Boolean(bot.data?.demo), title: callTitle(bot),
          attachedTo: bot.attached ? String(bot.owner?.email ?? 'a teammate') : '',
          status: String(bot.status ?? 'requested'), segments: [], nextIndex: 0, liveCursor: '', liveFeed: true, error: '',
        }))
        quiet(openPanel($, panelPath(await read($))))
      }
    } catch { /* following is a convenience; the tool's answer stands */ }
    return ran
  })

  on('tool.call', { tool: CONFIGURE_TOOL }, async ($, e) => ({ result: JSON.stringify(await configure($, e)) }))
    .catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener control failed; try again.' }))

  on('tool.call', { tool: CALIBRATE_TOOL }, async ($, e) => {
    const input = e as any
    const l = await readListener($)
    const rules = typeof input.rules === 'string' && input.rules.trim() ? input.rules.trim() : rulesText(l.brief)
    return { result: JSON.stringify(await calibrate($, rules, typeof input.fixture === 'string' ? input.fixture : '')) }
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener calibration failed.' }))

  // The pane's own calls are the person's button presses: approved. A model call is approved
  // only for a read-only tool or the listener's own controls; speaking and leaving always ask the
  // person; the rest goes to the session's decider as it would without us.
  on('tool.check', async ($, e, next) => {
    if (e.tool === CONFIGURE_TOOL || e.tool === CALIBRATE_TOOL) return { decision: 'allow', reason: "Vexa Live's own control" }
    // The plugin opening its web panel in the browser pane, for the panel origin it just got from
    // panel_link: the person decides (founder, 24 Oct), in Claude Code's own dialog — a hook's ask
    // goes to a person, not to auto mode's classifier. Once they said Yes, the same origin opens silently.
    if (e.tool === BROWSER_TOOL && next.origin.plugin === $.plugin.name && panelOrigin
      && originOf((e.input as { url?: unknown } | undefined)?.url) === panelOrigin) {
      if (panelAllowed.includes(panelOrigin)) return { decision: 'allow', reason: 'Vexa call panel, opened before with your Yes' }
      return { decision: 'ask', reason: 'Vexa wants to open its call panel beside the chat' }
    }
    // This plugin's own calls to the person's Vexa (the listener's reads, the band's presses) are
    // the grant; what the model calls on that server is the session's to decide, as without us.
    if (prefix && e.tool.startsWith(prefix) && next.origin.plugin === $.plugin.name) {
      return { decision: 'allow', reason: "Vexa Live's own call" }
    }
    return next(e)
  }).catch(($, e, next) => next(e))

  // The band above the prompt: ONE row of fixed height. A dot and one line of status cut at the
  // edge, then two buttons; the line changes text, never size. It offers to listen while a
  // meeting is live, says «Listening», and shows what woke Claude until the chat answers.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const l = await readListener($)
    const m = await read($)
    if (e.props.hasSurvey || (!l.isOn && !l.offer)) return next(e)
    const ui = $.ui.resolve(e) as any
    const { Box, Text, Button } = ui
    const f = await readFlash($)
    const hot = l.isOn && ['woke', 'queued', 'sent', 'answering'].includes(f.stage)
    // Founder, 24 Oct: four states only — «Listening», the trigger itself (≤3 words), «Answering»,
    // «Not listening» — in footnote size, with no «Claude» in the text.
    const line = !l.isOn ? `${l.offer?.title ?? (m.title || m.nativeId)} · Not listening`
      : f.stage === 'answering' ? 'Answering'
        : hot ? (f.line || 'Listening')
          : 'Listening'
    return (
      <Box flexDirection="row" gap={1} alignItems="center">
        {pulse(ui, 'band-dot', l.isOn)}
        <Box flexGrow={1} flexShrink={1} minWidth={0}>
          <Text color={!l.isOn ? 'inactive' : undefined} weight={hot ? 'semibold' : undefined} size="footnote" wrap="truncate-end">{line}</Text>
        </Box>
        {l.isOn ? <Button key="missed" label="What did I miss?" onPress={() => whatDidIMiss($)} /> : null}
        {l.isOn
          ? <Button key="listen-stop" label="Stop" onPress={() => stopListening($)} />
          : <Button key="listen" label="Listen" hotkey="l" variant="primary" onPress={() => listen($)} />}
        {!l.isOn ? <Button key="decline" plain onPress={() => decline($)}><Text color="inactive">No</Text></Button> : null}
      </Box>
    )
  })
}
