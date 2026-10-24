// Vexa pane: ONE pane beside the chat, opened at session start, driving the plugin's own `vexa` MCP
// server. Not signed in → a «Sign in» state; signed in → views switched by a row of buttons at its
// top (Home, Meeting, Calls, Calendar, Workspaces), since the engine cannot bring an already-open
// tab to the front. The live transcript follows with drafts in place until they are final.
//
// Permissions: the pane's own calls and Vexa's read-only tools run without a prompt. Anything the
// model calls that acts in a meeting (speak_in_meeting, stop_bot, request_meeting_bot, …) keeps
// the session's normal prompt.
//
// Listener: when a meeting of the person's is live, a band above the prompt offers to listen.
// Only after they press Listen, each change in the transcript's tail goes to a small model with
// the listen brief; when it says wake, the main Claude gets a turn with the excerpt and why. The
// main Claude tunes it with `listener_configure` and measures it with `listener_calibrate`.
//
// App: pane copy and listener presets come from an app folder (vexa-app.json): the project's
// `.vexa/app/`, else the person's `~/.vexa/apps/<name>/` (seeded on first run, merged with the
// plugin's newer version on update without overwriting their edits), else the plugin's own.
import type { Register } from 'claude-code'

import type { AppInfo, Call, Calendar, Calls, Flash, Listener, Meeting, Seg, Upcoming, View, Workspace, Workspaces } from '../types'
import {
  DECIDE_SYSTEM, FALLBACK_APP, briefFor, buildApp, calibrationSummary, decidePrompt, isDirect,
  norm, normalize, parseDecision, parseManifest, ruleHit, rulesText,
} from './listener'
import type { App, Labelled, Row } from './listener'

const PANE = 'vexa-home'
const SERVER_KEY = 'vexa'
const EMPTY: Meeting = {
  connection: 'connecting', url: '', platform: '', nativeId: '', dbId: 0, offers: { calendar: false, workspaces: false, demo: false }, title: '', isDemo: false,
  status: 'idle', nextIndex: 0, liveCursor: '', liveFeed: true, segments: [], error: '',
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
  offer: null, declined: [], isOn: false, preset: FALLBACK_APP.defaultPreset,
  brief: briefFor(FALLBACK_APP, FALLBACK_APP.defaultPreset), triaged: [], lastWakeAt: 0, wakes: [],
  lastVerdict: '', lastMs: 0, error: '', app: info(FALLBACK_APP),
}
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

// The pane's other values, one pair of helpers each (a state reference must be a literal).
const VIEW = { plugin: 'vexa', key: 'view' } as const
const CALLS = { plugin: 'vexa', key: 'calls' } as const
const CALENDAR = { plugin: 'vexa', key: 'calendar' } as const
const WORKSPACES = { plugin: 'vexa', key: 'workspaces' } as const
const NO_CALLS: Calls = { items: [], filter: '', error: '' }
const NO_CALENDAR: Calendar = { connected: [], upcoming: [], link: '', steps: [], error: '', loaded: false }
const NO_WORKSPACES: Workspaces = { items: [], slug: '', files: [], path: '', content: '', bound: '', error: '', loaded: false }
const FLASH = { plugin: 'vexa', key: 'flash' } as const
const NO_FLASH: Flash = { stage: 'idle', at: 0, line: '' }

async function readView($: any): Promise<View> {
  const { value } = await $.state.get(VIEW)
  return value ?? { name: 'home' }
}
async function updateView($: any, fn: (v: View) => View): Promise<void> {
  for (;;) {
    const held = await $.state.get(VIEW)
    if ((await $.state.set(VIEW, fn(held.value ?? { name: 'home' }), { ifVersion: held.version })).isSet) return
  }
}
async function readCalls($: any): Promise<Calls> {
  const { value } = await $.state.get(CALLS)
  return value ?? NO_CALLS
}
async function updateCalls($: any, fn: (c: Calls) => Calls): Promise<void> {
  for (;;) {
    const held = await $.state.get(CALLS)
    if ((await $.state.set(CALLS, fn(held.value ?? NO_CALLS), { ifVersion: held.version })).isSet) return
  }
}
async function readCalendar($: any): Promise<Calendar> {
  const { value } = await $.state.get(CALENDAR)
  return value ?? NO_CALENDAR
}
async function updateCalendar($: any, fn: (c: Calendar) => Calendar): Promise<void> {
  for (;;) {
    const held = await $.state.get(CALENDAR)
    if ((await $.state.set(CALENDAR, fn(held.value ?? NO_CALENDAR), { ifVersion: held.version })).isSet) return
  }
}
async function readWorkspaces($: any): Promise<Workspaces> {
  const { value } = await $.state.get(WORKSPACES)
  return value ?? NO_WORKSPACES
}
async function updateWorkspaces($: any, fn: (w: Workspaces) => Workspaces): Promise<void> {
  for (;;) {
    const held = await $.state.get(WORKSPACES)
    if ((await $.state.set(WORKSPACES, fn(held.value ?? NO_WORKSPACES), { ifVersion: held.version })).isSet) return
  }
}
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

const FILTER_MODEL = 'haiku'
const WAKE_GAP_MS = 45_000
const FOLLOW_MS = 25_000
const MAX_WAKES = 30
const WINDOW = 6
const OFFER_EVERY_MS = 30_000
const CONFIGURE = 'listener_configure'
const CALIBRATE = 'listener_calibrate'
const CONFIGURE_TOOL = 'mcp__vexa__listener_configure'
const CALIBRATE_TOOL = 'mcp__vexa__listener_calibrate'
const PROJECT_APP = '.vexa/app'
const LIVE_WAIT_MS = 1200

// Tools that only read. A model call to any of them is approved; everything else asks.
export const READ_ONLY = new Set([
  'whats_waiting', 'parse_meeting_link', 'list_meetings', 'get_meeting', 'get_meeting_transcript',
  'get_meeting_participants', 'get_meeting_chat', 'search_transcripts', 'list_recordings',
  'get_recording', 'get_bot_status', 'timeline', 'reactions_list', 'flows_list', 'friction_so_far',
  'bot_accounts_list', 'open_meeting', 'calendar_status', 'workspace_list', 'workspace_read', 'workspace_search',
])

// Tools whose effect other people see or that end the call: a model call always asks the person,
// whatever the mode (a hook's ask is a person's; no auto-mode check settles it).
export const ASK_PERSON: Record<string, string> = {
  speak_in_meeting: 'Vexa will say this out loud to everyone in the meeting.',
  stop_bot: 'Vexa will leave the meeting and stop transcribing it.',
}

// Where Claude keeps what it learns, as the person said it in chat (kept across sessions in
// $.store, so the next session knows): `cloud` = a Vexa workspace (the account's default write
// target, the same one the web's «Writes to …» shows), `local` = a folder on this computer,
// `ask` = only when they ask; unset = not asked yet.
type Settings = { write: '' | 'cloud' | 'local' | 'ask'; workspace: string; localPath: string }
const NO_SETTINGS: Settings = { write: '', workspace: '', localPath: '' }
let settings: Settings = NO_SETTINGS
// What the session-start context says about the server: its «Writes to …» line and workspaces.
let writesLine = ''
let workspaceNames: string[] = []
const SETTINGS_TOOL = 'mcp__vexa__vexa_settings'
const reported = new Set<number>()

// The module's own: a reload reconnects and reloads the app.
let server = ''
let polling = false
let app: App = FALLBACK_APP
let appDir = ''

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

// Background work (timers, loads a view starts): a failure shows in the view's own error line;
// a session ending under it rejects nothing.
function quiet(p: Promise<unknown>): void {
  p.catch(() => {})
}

function message(err: unknown): string {
  return String((err as Error)?.message ?? err).slice(0, 300)
}

async function connect($: any): Promise<void> {
  const r = await $.mcp.connect(SERVER_KEY)
  if (r.isConnected) {
    const was = (await read($)).connection
    server = r.server
    prefix = toolPrefix(r.server)
    const names = new Set((await $.tool.list()).map((t: { name: string }) => t.name))
    const offers = {
      calendar: names.has(`${prefix}calendar_status`),
      workspaces: names.has(`${prefix}workspace_list`),
      demo: names.has(`${prefix}start_demo_meeting`),
    }
    await update($, x => ({ ...x, connection: 'ready', offers, error: '' }))
    if (was !== 'ready') { quiet(loadCalls($)); if (offers.workspaces) quiet(loadWorkspaces($)) }
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

// One block per run of the same speaker, final text then the draft, as the app shows it.
export function runs(segs: readonly Seg[]): { speaker: string; time: string; text: string; draft: string }[] {
  const out: { speaker: string; time: string; text: string; draft: string }[] = []
  for (const s of segs) {
    const last = out[out.length - 1]
    if (last && last.speaker === s.speaker) {
      if (s.pending) last.draft = (last.draft ? last.draft + ' ' : '') + s.text
      else last.text = (last.text ? last.text + ' ' : '') + s.text
    } else out.push({ speaker: s.speaker, time: s.time, text: s.pending ? '' : s.text, draft: s.pending ? s.text : '' })
  }
  return out
}

// A segment's time in the person's own clock, as the call's meta line shows it.
function clockTime(t: unknown): string {
  if (t === undefined || t === null || t === '') return ''
  const d = new Date(typeof t === 'number' ? t : String(t))
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleTimeString('en', { hour: '2-digit', minute: '2-digit', hour12: false })
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
  if (m.connection !== 'ready' || !(isLive(m) || m.status === 'past') || pollingNow) return
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
      if (m.status !== 'past' && status === 'completed') await afterCall($, { ...m, status })
    }
  } catch (err) {
    await update($, x => ({ ...x, error: message(err) }))
  } finally {
    pollingNow = false
  }
}

async function start($: any, tool: string, args: Record<string, unknown>, isDemo: boolean): Promise<void> {
  await update($, x => ({ ...x, isDemo, title: isDemo ? 'Demo meeting' : '', status: 'requesting', error: '', segments: [], nextIndex: 0, liveCursor: '', liveFeed: true, nativeId: '', dbId: 0 }))
  try {
    const bot = await call($, tool, args)
    await update($, x => ({
      ...x,
      platform: String(bot.platform ?? ''),
      nativeId: String(bot.native_meeting_id ?? ''),
      dbId: Number(bot.id ?? bot.meeting_db_id ?? 0),
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
  await updateListener($, x => ({ ...x, isOn: false }))
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
    $.ui.toast(`Vexa: your app is in ${dir}. Edit its listener presets and pane there.`)
    return dir
  }
  const now = await $.fs.read(`${shipped}/vexa-app.json`)
  const was = (await $.fs.exists(`${base}/vexa-app.json`)) ? await $.fs.read(`${base}/vexa-app.json`) : ''
  if (now === was) return dir
  const kept: string[] = []
  for (const f of await files($, shipped)) {
    const next = await $.fs.read(`${shipped}/${f}`)
    const mine = (await $.fs.exists(`${dir}/${f}`)) ? await $.fs.read(`${dir}/${f}`) : null
    const old = (await $.fs.exists(`${base}/${f}`)) ? await $.fs.read(`${base}/${f}`) : null
    if (mine === null || mine === old || mine === next) await $.fs.write(`${dir}/${f}`, next)
    else if (next !== old) { await $.fs.write(`${dir}/${f}.upstream`, next); kept.push(f) }
  }
  await copyTree($, shipped, base)
  if (kept.length) $.ui.toast(`Vexa app updated; kept your edits to ${kept.join(', ')} (new versions beside them as .upstream).`)
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
async function seedProject($: any): Promise<string> {
  if (await $.fs.exists(`${PROJECT_APP}/vexa-app.json`)) return `${PROJECT_APP} already holds an app; nothing copied.`
  const n = await copyTree($, appDir && appDir !== PROJECT_APP ? appDir : `${$.plugin.root}/app`, PROJECT_APP)
  await loadApp($)
  return `Copied the Vexa app (${n} files) to ${PROJECT_APP}. Edit its listener presets and pane.json, then commit it.`
}

// Puts the person's copy back to the plugin's version, keeping theirs as a dated backup.
async function resetApp($: any): Promise<string> {
  const mine = await userApp($)
  if (!mine) return 'No app folder to reset.'
  const stamp = new Date(await $.clock.now()).toISOString().replace(/[:.]/g, '-')
  await copyTree($, mine, `${mine}.bak-${stamp}`)
  await copyTree($, `${$.plugin.root}/app`, mine)
  await copyTree($, `${$.plugin.root}/app`, `${mine}/.upstream`)
  await loadApp($)
  return `Reset ${mine} to the plugin's version; your copy is in ${mine}.bak-${stamp}.`
}

// --- Listener ---------------------------------------------------------------------------------

const roomKey = (r: { platform: string; nativeId: string }) => `${r.platform}/${r.nativeId}`

// A live meeting of the person's, not yet offered or declined, becomes the band's offer.
let lastOfferCheck = 0
async function findLive($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  const now = await $.clock.now()
  if (!l.app.pane.offerListening || l.isOn || l.offer || now - lastOfferCheck < OFFER_EVERY_MS) return
  lastOfferCheck = now
  if (m.nativeId && m.status === 'active' && !l.declined.includes(roomKey(m))) {
    await updateListener($, x => ({ ...x, offer: { platform: m.platform, nativeId: m.nativeId, title: m.title || m.nativeId } }))
    return
  }
  try {
    const p = await call($, 'list_meetings', { status: 'active', limit: 5 })
    const rows: any[] = Array.isArray(p) ? p : p.meetings ?? p.items ?? []
    const live = rows.map(r => ({
      platform: String(r.platform ?? ''),
      nativeId: String(r.native_meeting_id ?? r.native_id ?? ''),
      title: String(r.data?.title ?? r.title ?? r.native_meeting_id ?? 'Your meeting'),
    })).find(r => r.platform && r.nativeId && !l.declined.includes(roomKey(r)))
    if (live) await updateListener($, x => (x.isOn || x.offer ? x : { ...x, offer: live }))
  } catch { /* offering is best effort; the next check tries again */ }
}

// Listen: the pane follows the offered meeting, what is already said counts as heard, and the
// main Claude is told so it can tune the brief to the conversation.
async function listen($: any): Promise<void> {
  const l = await readListener($)
  const room = l.offer
  if (!room) return
  const m = await read($)
  if (m.platform !== room.platform || m.nativeId !== room.nativeId) {
    await update($, x => ({ ...x, platform: room.platform, nativeId: room.nativeId, title: room.title, isDemo: false, status: 'active', segments: [], nextIndex: 0, liveCursor: '', liveFeed: true }))
    await poll($)
  }
  const heard = (await read($)).segments.filter(s => s.id).map(s => s.id)
  lastAuditAt = await $.clock.now()
  auditCount = heard.length
  await updateListener($, x => ({ ...x, offer: null, isOn: true, triaged: heard.slice(-500), wakes: [], lastWakeAt: 0, error: '' }))
  void $.prompt.submit({
    text: `[Vexa listener] The person pressed Listen on "${room.title}" (${room.platform} ${room.nativeId}). `
      + `I watch its transcript and wake you when something matters under the "${l.preset}" listen brief. `
      + `If you can tell what kind of conversation this is, tune it with ${CONFIGURE}; otherwise acknowledge in one line.`,
  })
}

async function listenToPane($: any): Promise<void> {
  const m = await read($)
  if (!m.nativeId) return
  await updateListener($, x => ({ ...x, offer: { platform: m.platform, nativeId: m.nativeId, title: m.title || m.nativeId } }))
  await listen($)
}

async function decline($: any): Promise<void> {
  await updateListener($, x => (x.offer ? { ...x, declined: [...x.declined, roomKey(x.offer)].slice(-50), offer: null } : x))
}

async function stopListening($: any): Promise<void> {
  const m = await read($)
  await updateListener($, x => ({ ...x, isOn: false, declined: m.nativeId ? [...x.declined, roomKey(m)].slice(-50) : x.declined }))
}

async function setPreset($: any, preset: string): Promise<void> {
  if (!app.presets[preset]) return
  await updateListener($, x => ({ ...x, preset, brief: briefFor(app, preset) }))
}

async function decide($: any, rules: string, context: string[], fresh: string[]): Promise<{ wake: boolean; reason: string; ms: number }> {
  const t0 = await $.clock.now()
  const r = await $.model.complete({
    model: FILTER_MODEL,
    maxTokens: 2,
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
let batch: { reason: string; lines: string[]; direct: boolean }[] = []

// What the band shows the moment the listener acts, before the chat answers: woke → queued →
// sent → answering → done, back to idle 8 s after done.
async function setFlash($: any, stage: Flash['stage'], line?: string): Promise<void> {
  const at = await $.clock.now()
  await updateFlash($, f => ({ ...f, stage, at, line: line ?? f.line }))
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
  const n = batch.length
  batch = []
  busy = true
  await setFlash($, 'sent')
  const m = await read($)
  void $.prompt.submit({
    text: `[Vexa listener${n > 1 ? ` · ${n} wakes batched` : ''}] ${reasons}\n\nFrom the live meeting "${m.title || m.nativeId}" (${m.platform} ${m.nativeId}):\n`
      + `${lines.join('\n')}\n\nReact briefly if it matters to the person; tune the listener with ${CONFIGURE} if it woke you for nothing.`,
  })
}

// Judges the tail of the transcript, drafts included, whenever it changes. A rule trigger wakes
// with no model call; one wake per thought (segment id or the same words); wakes are WAKE_GAP_MS
// apart unless the speaker addressed the assistant, whose next lines within FOLLOW_MS may follow
// (the filter still has to say wake).
const WOKE_FOR = new Set<string>()
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
    const canWake = !already && v.wake && l.wakes.length < MAX_WAKES && (direct || followUp || Boolean(hit) || gapOk)
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
      await setFlash($, 'woke', fresh.map(s => s.text).join(' ').slice(-160))
      batch.push({ reason: v.reason, lines: [...context.slice(-2), ...lines], direct })
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
  return { fixture: path, model: FILTER_MODEL, summary, misses }
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
  return { listening: l.isOn, meeting, offer: l.offer, preset: l.preset, presets: l.app.presets, app: l.app.name, appFolder: appDir, brief: l.brief, wakes: l.wakes, lastVerdict: l.lastVerdict }
}

// The person's answer to «where should I keep what I learn», saved by Claude in chat. Cloud also
// sets the account's default write target on the server, so the web shows the same choice.
async function saveSettings($: any, input: any): Promise<unknown> {
  const write = ['cloud', 'local', 'ask'].includes(input.write) ? input.write : settings.write
  const next: Settings = {
    write,
    workspace: typeof input.workspace === 'string' ? input.workspace : settings.workspace,
    localPath: typeof input.local_path === 'string' ? input.local_path : settings.localPath,
  }
  if (input.write === undefined && input.workspace === undefined && input.local_path === undefined) return { settings, writes: writesLine }
  if (next.write === 'cloud') {
    if (!next.workspace) return { error: 'Cloud needs a workspace slug: workspace_list shows them; workspace_new makes one.' }
    await call($, 'workspace_defaults_set', { target: next.workspace })
  }
  if (next.write === 'local' && !next.localPath) {
    const home = await $.env.get('HOME')
    next.localPath = `${home ?? '~'}/Vexa`
  }
  settings = next
  await $.store.set('settings', settings)
  quiet(loadWorkspaces($))
  return { saved: settings }
}

// Two or three lines at the end of the system prompt: what Vexa is connected to, where Claude
// writes and whose rules apply, or which setup question to ask next. Read from memory: no calls.
function contextText(m: Meeting): string {
  if (m.connection !== 'ready') {
    return 'Vexa (meeting bot, live transcripts, team workspaces) is installed but not signed in. If the person wants Vexa, '
      + 'tell them to type /mcp, choose plugin:vexa:vexa and Authenticate; then use the vexa-setup skill.'
  }
  const lines = [`Vexa is connected. Workspaces: ${workspaceNames.slice(0, 8).join('; ') || 'not loaded yet'}.`]
  if (settings.write === 'cloud') lines.push(`You write what you learn to the Vexa workspace «${settings.workspace}» (${writesLine || 'the account default'}). Before writing, read its rules: workspace_read CLAUDE.md and workspace_purpose; follow the vexa-workspace skill.`)
  else if (settings.write === 'local') lines.push(`You write what you learn as markdown under ${settings.localPath} (the vexa-workspace layout). Read ${settings.localPath}/CLAUDE.md first if it exists.`)
  else if (settings.write === 'ask') lines.push('Write to a workspace only when the person asks; then follow the vexa-workspace skill.')
  else lines.push('Setup is not finished: when it fits, ask in chat «Where should I keep what I learn from your calls: on this computer, or in a cloud workspace you can share?» and save the answer with vexa_settings (vexa-setup skill).')
  return lines.join('\n')
}

// After a real call ends, when writing is allowed: one turn for the main Claude to file it.
async function afterCall($: any, m: Meeting): Promise<void> {
  if (!m.dbId || m.isDemo || reported.has(m.dbId) || !['cloud', 'local'].includes(settings.write)) return
  reported.add(m.dbId)
  const where = settings.write === 'cloud' ? `the Vexa workspace «${settings.workspace}»` : `the local folder ${settings.localPath}`
  void $.prompt.submit({
    text: `[Vexa] The call «${m.title || m.nativeId}» (${m.platform} ${m.nativeId}, meeting_db_id ${m.dbId}) just ended. `
      + `Writing is allowed to ${where}. Follow the vexa-workspace skill: read the workspace's rules first, then write the notes, `
      + `decisions, commitments and the people in it, link the meeting page to them, and reply with one line and a link. Skip personal talk.`,
  })
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
  },
}

// --- Calls, calendar, workspaces: what the views beyond the meeting read --------------------

function callTitle(x: any): string {
  return String(x.data?.title || (x.data?.demo ? 'Demo meeting' : x.platform === 'google_meet' ? 'Google Meet call' : x.platform === 'teams' ? 'Teams call'
    : x.platform === 'zoom' ? 'Zoom call' : x.platform === 'jitsi' ? 'Jitsi call' : x.platform === 'vexa_call' ? 'Vexa call' : 'Call'))
}

async function loadCalls($: any): Promise<void> {
  try {
    const p = await call($, 'list_meetings', { limit: 60 })
    const rows: any[] = Array.isArray(p) ? p : p.meetings ?? p.items ?? []
    const items: Call[] = rows.map(x => ({
      id: Number(x.id ?? x.meeting_db_id ?? 0), platform: String(x.platform ?? ''),
      nativeId: String(x.native_meeting_id ?? ''), status: String(x.status ?? ''), title: callTitle(x),
      at: String(x.start_time ?? x.created_at ?? ''),
    }))
    await updateCalls($, c => ({ ...c, items, error: '' }))
  } catch (err) { await updateCalls($, c => ({ ...c, error: message(err) })) }
}

async function openCall($: any, c: Call): Promise<void> {
  await update($, x => ({
    ...EMPTY, connection: x.connection, offers: x.offers, platform: c.platform, nativeId: c.nativeId, dbId: c.id, title: c.title,
    // A past call is read once, confirmed lines only; its answer's own status then ends the polling.
    status: c.status === 'active' ? 'active' : 'past', liveFeed: c.status === 'active',
  }))
  await go($, 'meeting')
  await poll($)
}

async function loadCalendar($: any): Promise<void> {
  try {
    const p = await call($, 'calendar_status', { days: 7 })
    const connected: string[] = (p.calendars ?? []).map((c: any) => String(c.name || c.ics_url_masked || 'Calendar'))
    const upcoming: Upcoming[] = (p.upcoming ?? []).map((e: any) => ({
      id: Number(e.meeting_db_id ?? 0), title: String(e.title || 'Meeting'), start: String(e.scheduled_at ?? ''),
      platform: String(e.platform ?? ''), nativeId: String(e.native_meeting_id ?? ''),
      autoJoin: Boolean(e.will_auto_join ?? e.auto_join), whyNot: String(e.why_not ?? e.auto_join_error ?? ''),
    }))
    const error = p.upcoming_unavailable ? String(p.upcoming_unavailable) : ''
    await updateCalendar($, c => ({ ...c, connected, upcoming, error, loaded: true }))
  } catch (err) { await updateCalendar($, c => ({ ...c, error: message(err), loaded: true })) }
}

async function connectCalendar($: any): Promise<void> {
  try {
    const p = await call($, 'calendar_connect', {})
    await updateCalendar($, c => ({ ...c, link: String(p.url ?? ''), steps: (p.steps ?? []).map(String), error: '' }))
  } catch (err) { await updateCalendar($, c => ({ ...c, error: message(err) })) }
}

async function setAutoJoin($: any, target: Upcoming | null, on: boolean): Promise<void> {
  try {
    await call($, 'set_auto_join', target ? { enabled: on, meeting_db_id: target.id } : { enabled: on, all_calendars: true })
    await loadCalendar($)
  } catch (err) { await updateCalendar($, c => ({ ...c, error: message(err) })) }
}

// --- Workspaces: list them, open one at its README and its .md pages, bind the open meeting --

async function loadWorkspaces($: any): Promise<void> {
  try {
    const p = await call($, 'workspace_list', {})
    writesLine = String(p.line ?? '')
    const items: Workspace[] = (p.workspaces ?? []).filter((w: any) => (w.state ?? 'active') === 'active').map((w: any) => ({
      slug: String(w.slug ?? w.id ?? w.name), name: String(w.display ?? w.name ?? w.title ?? w.slug ?? w.id),
      kind: String(w.kind ?? w.type ?? ''), role: String(w.role ?? ''),
    }))
    workspaceNames = items.map(x => `${x.name} (${x.slug}, ${x.kind || 'workspace'}${x.role ? `, ${x.role}` : ''})`)
    await updateWorkspaces($, w => ({ ...w, items, error: '', loaded: true }))
  } catch (err) { await updateWorkspaces($, w => ({ ...w, error: message(err), loaded: true })) }
}

async function openFile($: any, slug: string, path: string): Promise<void> {
  await updateWorkspaces($, w => ({ ...w, slug, path, content: '', error: '' }))
  try {
    const f = await call($, 'workspace_read', { slug, path })
    await updateWorkspaces($, w => (w.slug === slug ? { ...w, content: String(f.content ?? f.text ?? f.body ?? '') } : w))
  } catch (err) { await updateWorkspaces($, w => ({ ...w, error: message(err) })) }
}

async function openSpace($: any, slug: string): Promise<void> {
  await updateWorkspaces($, w => ({ ...w, slug, files: [], path: '', content: '', error: '' }))
  await go($, 'space')
  try {
    const t = await call($, 'workspace_tree', { slug })
    const files: string[] = (t.files ?? t.pages ?? t.tree ?? []).map((f: any) => String(typeof f === 'string' ? f : f.path ?? '')).filter((f: string) => f.endsWith('.md'))
    await updateWorkspaces($, w => ({ ...w, files }))
    const first = files.find(f => /^readme\.md$/i.test(f)) ?? files.find(f => !f.includes('/')) ?? files[0]
    if (first) await openFile($, slug, first)
  } catch (err) { await updateWorkspaces($, w => ({ ...w, error: message(err) })) }
}

async function bindMeeting($: any, workspace: string): Promise<void> {
  const m = await read($)
  if (!m.dbId) return
  try {
    await call($, 'meeting_bind_workspace', { meeting_db_id: m.dbId, workspace })
    await updateWorkspaces($, w => ({ ...w, bound: `${m.title || m.nativeId} → ${workspace}`, error: '' }))
  } catch (err) { await updateWorkspaces($, w => ({ ...w, error: message(err) })) }
}

// --- The one Vexa pane: views switch inside it (the engine cannot bring an open tab to the front) --

type ViewName = View['name']
const ASKS = [
  { key: 'ask-last', label: 'Summarize my last call', text: 'Summarize my most recent Vexa call: decisions, asks to me, numbers, next steps. Use the Vexa tools to read its transcript.' },
  { key: 'ask-promises', label: 'What did I promise this week?', text: 'Go through my Vexa calls from the last 7 days and list every commitment I made, with the call and date.' },
  { key: 'ask-find', label: 'Find a topic in my calls', text: 'I want to find something said in my Vexa calls. Ask me what to look for, then search the transcripts.' },
]

// Colours (terminal design guidelines 2026-10-10 §1–2, founder 24 Oct): monochrome theme keys,
// emphasis from weight, never red, sparing with blue; green means live and nothing else.
const LIVE = 'success'
const PLATFORM: Record<string, string> = { google_meet: 'Meet', teams: 'Teams', zoom: 'Zoom', jitsi: 'Jitsi', vexa_call: 'Vexa call' }

async function go($: any, v: ViewName): Promise<void> {
  await updateView($, () => ({ name: v }))
  await $.ui.open({ id: PANE, title: app.pane.title })
  if (v === 'calls') quiet(loadCalls($))
  if (v === 'calendar') quiet(loadCalendar($))
  if (v === 'workspaces') quiet(loadWorkspaces($))
}

function day(at: string): string {
  return at ? new Date(at).toLocaleString('en', { month: 'short', day: 'numeric' }) : ''
}
function hour(at: string): string {
  return at ? new Date(at).toLocaleString('en', { hour: '2-digit', minute: '2-digit', hour12: false }) : ''
}
function when(at: string): string {
  return at ? new Date(at).toLocaleString('en', { weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) : ''
}

// A pulsing dot (desktop draws SVG with SMIL animation; the terminal falls back to a glyph).
function pulse(ui: any, key: string, isOn: boolean): any {
  const { Svg, Text } = ui
  if (!Svg) return <Text key={key} color={isOn ? LIVE : 'inactive'}>●</Text>
  const anim = isOn ? '<animate attributeName="r" values="3;5;3" dur="1.4s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;0.45;1" dur="1.4s" repeatCount="indefinite"/>' : ''
  return <Svg key={key} alt={isOn ? 'live' : 'off'} width={14} height={14} isInteractive={isOn ? true : undefined} source={`<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 14 14"><circle cx="7" cy="7" r="4" fill="${isOn ? '#2f9e72' : '#9AA0A6'}">${anim}</circle></svg>`} />
}

// Speech meter: bars that move while text is arriving, flat when the room is quiet.
function meter(ui: any, key: string, isTalking: boolean): any {
  const { Svg } = ui
  if (!Svg) return null
  const bars = [0, 1, 2, 3, 4].map(i => {
    const x = 2 + i * 6
    const a = isTalking ? `<animate attributeName="height" values="3;${8 + (i % 3) * 4};3" dur="${0.6 + i * 0.13}s" repeatCount="indefinite"/><animate attributeName="y" values="8.5;${7 - (i % 3) * 2};8.5" dur="${0.6 + i * 0.13}s" repeatCount="indefinite"/>` : ''
    return `<rect x="${x}" y="8.5" width="3" height="3" rx="1" fill="${isTalking ? '#2f9e72' : '#9AA0A6'}">${a}</rect>`
  }).join('')
  return <Svg key={key} alt={isTalking ? 'people are talking' : 'quiet'} width={32} height={20} isInteractive={isTalking ? true : undefined} source={`<svg xmlns="http://www.w3.org/2000/svg" width="32" height="20" viewBox="0 0 32 20">${bars}</svg>`} />
}

// The nav: the current view is the default (grey pill) Button; the others are quiet text.
function nav($: any, ui: any, v: ViewName, offers: Meeting['offers']): any {
  const { Box, Button, Text } = ui
  const cur = v === 'space' ? 'workspaces' : v
  const tab = (id: ViewName, label: string) => (
    cur === id
      ? <Button key={`nav-${id}`} label={label} onPress={() => go($, id)} />
      : <Button key={`nav-${id}`} plain onPress={() => go($, id)}><Text color="inactive">{label}</Text></Button>
  )
  return (
    <Box flexDirection="row" gap={1} alignItems="center" flexWrap="wrap">
      {tab('home', 'Home')}
      {tab('calls', 'Calls')}
      {offers.workspaces ? tab('workspaces', 'Workspaces') : null}
      {offers.calendar ? tab('calendar', 'Calendar') : null}
      {tab('meeting', 'Meeting')}
    </Box>
  )
}

// A card, kept for «Send Vexa to a call» and the live call only: neutral border, title, meta.
function card(ui: any, key: string, title: any, meta: string, onPress?: () => unknown, extra?: any): any {
  const { Box, Button, Text } = ui
  return (
    <Box key={key} flexDirection="column" borderStyle="round" borderColor="subtle" paddingX={1}>
      {onPress
        ? <Button key={`${key}-b`} plain onPress={onPress}><Text weight="semibold">{title}</Text></Button>
        : <Text weight="semibold">{title}</Text>}
      {meta ? <Text color="inactive" size="caption">{meta}</Text> : null}
      {extra ?? null}
    </Box>
  )
}

// A section label: sentence case, caption size, medium weight, quiet.
function heading(ui: any, key: string, label: string, more?: { label: string; onPress: () => unknown }): any {
  const { Box, Text, Button } = ui
  return (
    <Box key={key} flexDirection="row" justifyContent="space-between" alignItems="center">
      <Text color="inactive" size="caption" weight="medium">{label}</Text>
      {more ? <Button key={`${key}-more`} plain onPress={more.onPress}><Text color="inactive" size="caption">{more.label}</Text></Button> : null}
    </Box>
  )
}

// A list row (guidelines §4.8): a pressable title, a quiet meta line under it, no border.
function row(ui: any, key: string, title: string, meta: string, onPress?: () => unknown, isLive = false, extra?: any): any {
  const { Box, Button, Text } = ui
  return (
    <Box key={key} flexDirection="column">
      <Box flexDirection="row" gap={1} alignItems="center">
        {isLive ? pulse(ui, `${key}-dot`, true) : null}
        {onPress
          ? <Button key={`${key}-b`} plain onPress={onPress}><Text weight="medium">{title}</Text></Button>
          : <Text weight="medium">{title}</Text>}
        {extra ?? null}
      </Box>
      {meta ? <Text color="inactive" size="caption">{meta}</Text> : null}
    </Box>
  )
}

function callRow($: any, ui: any, x: Call, keyPrefix: string): any {
  const live = x.status === 'active'
  return row(ui, `${keyPrefix}-${x.id}`, x.title,
    [live ? 'Live' : '', day(x.at), hour(x.at), PLATFORM[x.platform] ?? x.platform].filter(Boolean).join(' · '),
    () => openCall($, x), live)
}

function problem(ui: any, key: string, text: string): any {
  const { Text } = ui
  return text ? <Text key={key} weight="medium" size="footnote">{text.split('\n')[0]}</Text> : null
}

async function homeView($: any, ui: any): Promise<any> {
  const { Box, Text, Button, Input } = ui
  const c = await readCalls($)
  const m = await read($)
  const l = await readListener($)
  const w = await readWorkspaces($)
  const cal = await readCalendar($)
  const live = c.items.filter(x => x.status === 'active')
  const recent = c.items.filter(x => x.status !== 'active').slice(0, 3)
  return (
    <Box flexDirection="column" gap={1}>
      {card(ui, 'h-send', 'Send Vexa to a call', 'Paste a Meet, Teams or Zoom link. Let the bot in; the transcript streams here.', undefined, (
        <Box flexDirection="column">
          {Input ? <Input key="h-url" placeholder="Meeting link" value={m.url} submitLabel="Send" onInput={(v: string) => update($, x => ({ ...x, url: v.trim() }))} onSubmit={async (v: string) => { await send($, v); await go($, 'meeting') }} /> : null}
          {l.app.pane.demo && m.offers.demo ? (
            <Box flexDirection="row" gap={1} alignItems="center" flexWrap="wrap">
              <Button key="h-demo" label="Try a demo" hotkey="d" onPress={async () => { await start($, 'start_demo_meeting', {}, true); await go($, 'meeting') }} />
              <Text color="inactive" size="caption">No meeting now? A presenter speaks for a minute while you watch it transcribed.</Text>
            </Box>
          ) : null}
        </Box>
      ))}
      {live.map(x => card(ui, `h-live-${x.id}`, `Live · ${x.title}`,
        l.isOn && m.nativeId === x.nativeId ? `Claude is listening · woke ${l.wakes.length}×` : 'Claude is not listening',
        () => openCall($, x),
        <Box flexDirection="row" gap={1} flexWrap="wrap">
          <Button key={`h-live-open-${x.id}`} label="Open" variant="primary" onPress={() => openCall($, x)} />
          {m.nativeId === x.nativeId ? <Button key={`h-live-listen-${x.id}`} label={l.isOn ? 'Stop listening' : 'Listen'} onPress={() => (l.isOn ? stopListening($) : listenToPane($))} /> : null}
        </Box>))}
      {heading(ui, 'h-recent', 'Recent calls', { label: 'All', onPress: () => go($, 'calls') })}
      {recent.length ? recent.map(x => callRow($, ui, x, 'h-rec')) : <Text dimColor>No calls yet.</Text>}
      {m.offers.workspaces ? heading(ui, 'h-ws', 'Workspaces', { label: 'All', onPress: () => go($, 'workspaces') }) : null}
      {!m.offers.workspaces ? null : w.items.length ? w.items.slice(0, 3).map(x => row(ui, `h-ws-${x.slug}`, x.name, [x.kind, x.role].filter(Boolean).join(' · '), () => openSpace($, x.slug)))
        : w.error ? problem(ui, 'h-ws-err', w.error) : <Text dimColor>{w.loaded ? 'No workspaces yet.' : 'Loading…'}</Text>}
      {m.offers.calendar ? heading(ui, 'h-cal', 'Calendar', { label: 'Open', onPress: () => go($, 'calendar') }) : null}
      {m.offers.calendar && row(ui, 'h-cal-row', cal.connected.length ? `Vexa joins ${cal.upcoming.filter(e => e.autoJoin).length} of ${cal.upcoming.length} upcoming meetings` : 'Connect your calendar',
        'Vexa joins your meetings by itself; you choose which ones.', () => go($, 'calendar'))}
      {heading(ui, 'h-ask', 'Ask Claude')}
      <Box flexDirection="column">
        {ASKS.map(a => <Button key={a.key} plain onPress={() => $.prompt.submit({ text: a.text })}><Text>{a.label}</Text></Button>)}
      </Box>
    </Box>
  )
}

async function callsView($: any, ui: any): Promise<any> {
  const { Box, Text, Input } = ui
  const c = await readCalls($)
  const q = c.filter.toLowerCase()
  const list = c.items.filter(x => !q || x.title.toLowerCase().includes(q) || x.nativeId.toLowerCase().includes(q))
  const groups: Record<string, Call[]> = {}
  for (const x of list) {
    const k = x.status === 'active' ? 'Live' : x.at ? new Date(x.at).toLocaleString('en', { month: 'long', year: 'numeric' }) : 'Earlier'
    ;(groups[k] ??= []).push(x)
  }
  return (
    <Box flexDirection="column" gap={1}>
      {Input ? <Input key="calls-filter" placeholder="Filter calls" value={c.filter} onInput={(v: string) => updateCalls($, x => ({ ...x, filter: v }))} onSubmit={(v: string) => updateCalls($, x => ({ ...x, filter: v }))} /> : null}
      {problem(ui, 'calls-err', c.error)}
      {Object.entries(groups).map(([g, xs]) => (
        <Box key={`g-${g}`} flexDirection="column" gap={1}>
          {heading(ui, `gh-${g}`, g)}
          {xs.map(x => callRow($, ui, x, 'call'))}
        </Box>
      ))}
      {list.length === 0 ? <Text dimColor>No calls yet.</Text> : null}
    </Box>
  )
}

async function workspacesView($: any, ui: any): Promise<any> {
  const { Box, Text } = ui
  const w = await readWorkspaces($)
  return (
    <Box flexDirection="column" gap={1}>
      <Text color="inactive" size="caption">Shared pages Claude reads and writes: notes, decisions, people.</Text>
      {problem(ui, 'ws-err', w.error)}
      {w.items.map(x => row(ui, `ws-${x.slug}`, x.name, [x.kind, x.role].filter(Boolean).join(' · '), () => openSpace($, x.slug)))}
      {w.items.length === 0 && !w.error ? <Text dimColor>{w.loaded ? 'No workspaces yet.' : 'Loading…'}</Text> : null}
    </Box>
  )
}

async function spaceView($: any, ui: any): Promise<any> {
  const { Box, Text, Button, Markdown } = ui
  const w = await readWorkspaces($)
  const m = await read($)
  const ws = w.items.find(x => x.slug === w.slug)
  const top = w.files.filter(f => f !== w.path).slice(0, 24)
  return (
    <Box flexDirection="column" gap={1}>
      <Button key="ws-back" plain onPress={() => go($, 'workspaces')}><Text color="inactive">Workspaces</Text></Button>
      <Text size="heading" weight="semibold">{ws?.name ?? w.slug}</Text>
      {w.path ? <Text color="inactive" size="caption">{w.path}</Text> : null}
      {m.dbId ? (
        <Box flexDirection="row" gap={1} alignItems="center" flexWrap="wrap">
          <Button key="ws-bind" label="Bind the open meeting here" onPress={() => bindMeeting($, w.slug)} />
          <Text color="inactive" size="caption">{w.bound ? `Bound: ${w.bound}` : `${m.title || m.nativeId}: its notes land here`}</Text>
        </Box>
      ) : null}
      {problem(ui, 'ws-err', w.error)}
      {w.content ? (Markdown ? <Markdown key="ws-md" text={w.content.slice(0, 60000)} /> : <Text>{w.content.slice(0, 4000)}</Text>) : <Text dimColor>{w.files.length && !w.path ? '' : 'Loading…'}</Text>}
      {top.length ? heading(ui, 'ws-pages', 'Pages') : null}
      <Box flexDirection="column">
        {top.map(f => <Button key={`ws-f-${f}`} plain onPress={() => openFile($, w.slug, f)}><Text>{f}</Text></Button>)}
      </Box>
    </Box>
  )
}

async function calendarView($: any, ui: any): Promise<any> {
  const { Box, Text, Button, Link } = ui
  const c = await readCalendar($)
  const joining = c.upcoming.filter(e => e.autoJoin).length
  return (
    <Box flexDirection="column" gap={1}>
      <Text color="inactive" size="caption">Connect your calendar and Vexa joins your meetings by itself. Choose which ones.</Text>
      {heading(ui, 'c-conn', 'Calendars')}
      {c.connected.map((name, i) => row(ui, `cal-${i}`, name, 'Connected'))}
      {row(ui, 'c-add', c.connected.length ? 'Connect another calendar' : 'Connect your calendar', 'You sign in on vexa.ai yourself; Vexa never asks for your password here.', () => connectCalendar($))}
      {c.link ? (Link ? <Link key="c-link" href={c.link} label="Open the calendar page" /> : <Text>{c.link}</Text>) : null}
      {c.steps.map((st, i) => <Text key={`c-step-${i}`} color="inactive" size="caption">{`${i + 1}. ${st}`}</Text>)}
      {heading(ui, 'c-next', `Upcoming · Vexa joins ${joining} of ${c.upcoming.length}`, c.upcoming.length ? { label: joining === c.upcoming.length ? 'Join none' : 'Join all', onPress: () => setAutoJoin($, null, joining !== c.upcoming.length) } : undefined)}
      {c.upcoming.map(e => row(ui, `up-${e.id}`, e.title, [when(e.start), PLATFORM[e.platform] ?? e.platform, e.autoJoin ? 'Vexa joins' : e.whyNot || 'Vexa skips'].filter(Boolean).join(' · '), undefined, false,
        <Button key={`aj-${e.id}`} plain onPress={() => setAutoJoin($, e, !e.autoJoin)}><Text color="inactive" size="caption">{e.autoJoin ? 'Skip' : 'Join'}</Text></Button>))}
      {c.upcoming.length === 0 ? <Text dimColor>{c.loaded ? 'Nothing on the calendar soon.' : 'Loading…'}</Text> : null}
      {problem(ui, 'c-err', c.error)}
    </Box>
  )
}

async function meetingView($: any, ui: any, rows: number, cols: number): Promise<any> {
  const { Box, Text, Button, Input, Select } = ui
  const m = await read($)
  const l = await readListener($)
  const room = Math.max(4, Math.floor((rows - 10) / 2))
  const live = isLive(m) || m.status === 'requesting'
  const inCall = live && m.status === 'active'
  const now = await $.clock.now()
  const talking = inCall && lastTextAt > 0 && now - lastTextAt < 10_000
  const state = live ? (inCall ? 'In the call' : m.status) : m.nativeId ? 'Past call' : 'No meeting'
  // Wide pane: transcript on the left, the call's controls on the right. Narrow: one column.
  const wide = cols >= 80
  const controls = (
    <Box key="m-controls" flexDirection="column" gap={1} width={wide ? 34 : undefined} flexShrink={0}>
      <Box flexDirection="row" gap={1} alignItems="center">
        {inCall ? pulse(ui, 'm-dot', true) : null}
        <Text weight="semibold">{state}</Text>
        {inCall ? meter(ui, 'm-meter', talking) : null}
      </Box>
      {live ? (
        <Box flexDirection="column" gap={1}>
          <Text color="inactive" size="footnote">{l.isOn ? 'Claude is listening and speaks up when something matters.' : 'Claude is not listening.'}</Text>
          <Box flexDirection="row" gap={1} flexWrap="wrap" alignItems="center">
            {inCall ? <Button key="listen" label={l.isOn ? 'Stop listening' : 'Listen'} hotkey="l" variant={l.isOn ? 'secondary' : 'primary'} onPress={() => (l.isOn ? stopListening($) : listenToPane($))} /> : null}
            {l.isOn ? <Button key="m-missed" label="What did I miss?" onPress={() => whatDidIMiss($)} /> : null}
            {l.isOn && Select ? <Select key="m-mode" value={l.preset} options={l.app.presets.map(p => ({ value: p, label: p }))} onSelect={(v: string) => setPreset($, v)} /> : null}
            <Button key="stop" plain onPress={() => stop($)}><Text color="inactive">Leave call</Text></Button>
          </Box>
          {problem(ui, 'm-l-err', l.error)}
        </Box>
      ) : (
        <Box flexDirection="column" gap={1}>
          <Text color="inactive" size="footnote">{m.nativeId ? 'A finished call. Paste a new link to send Vexa to another one.' : 'Paste a Meet, Teams or Zoom link; the transcript appears once Vexa is let in.'}</Text>
          {Input ? <Input key="url" placeholder="Paste a meeting link" value={m.url} submitLabel="Send bot" onInput={(v: string) => update($, x => ({ ...x, url: v.trim() }))} onSubmit={(v: string) => send($, v)} /> : null}
          <Box flexDirection="row" gap={1} flexWrap="wrap">
            <Button key="send" label="Send Vexa to the call" variant="primary" onPress={() => send($, m.url)} />
            {l.app.pane.demo && m.offers.demo ? <Button key="demo" label="Try a demo" onPress={() => start($, 'start_demo_meeting', {}, true)} /> : null}
          </Box>
        </Box>
      )}
      {problem(ui, 'm-err', m.error)}
    </Box>
  )
  const transcript = (
    <Box key="m-transcript" flexDirection="column" gap={1} flexGrow={1}>
      {m.segments.length === 0 ? <Text dimColor>{live ? (m.isDemo ? 'The demo presenter starts speaking in a few seconds…' : 'Waiting for speech…') : m.nativeId ? 'Loading the transcript…' : 'The transcript will appear here.'}</Text> : null}
      {runs(m.segments).slice(-room).map((s, i) => (
        <Box key={`seg-${i}`} flexDirection="column">
          <Box flexDirection="row" gap={1}>
            <Text weight="semibold" size="footnote">{s.speaker}</Text>
            <Text color="inactive" size="caption" tabular>{s.time}</Text>
          </Box>
          {s.text ? <Text>{s.text}</Text> : null}
          {s.draft ? <Text dimColor italic>{s.draft}</Text> : null}
        </Box>
      ))}
    </Box>
  )
  // Header: back to the list, the call's title, and stepping to the newer / older call.
  const c = await readCalls($)
  const i = c.items.findIndex(x => x.nativeId === m.nativeId && x.platform === m.platform)
  const cur = i >= 0 ? c.items[i] : undefined
  const newer = i > 0 ? c.items[i - 1] : undefined
  const older = i >= 0 && i < c.items.length - 1 ? c.items[i + 1] : undefined
  const header = (
    <Box key="m-head" flexDirection="column">
      <Box flexDirection="row" justifyContent="space-between" alignItems="center">
        <Button key="m-back" plain onPress={() => go($, 'calls')}><Text color="inactive">Calls</Text></Button>
        <Box flexDirection="row" gap={1}>
          {newer ? <Button key="m-prev" plain onPress={() => openCall($, newer)}><Text color="inactive">Newer</Text></Button> : null}
          {older ? <Button key="m-next" plain onPress={() => openCall($, older)}><Text color="inactive">Older</Text></Button> : null}
        </Box>
      </Box>
      <Text size="heading" weight="semibold">{cur?.title ?? (m.title || `${m.isDemo ? 'Demo · ' : ''}${PLATFORM[m.platform] ?? m.platform} ${m.nativeId}`)}</Text>
      {cur ? <Text color="inactive" size="caption">{[day(cur.at), hour(cur.at), PLATFORM[cur.platform] ?? cur.platform].filter(Boolean).join(' · ')}</Text> : null}
    </Box>
  )
  return (
    <Box flexDirection="column" gap={1}>
      {m.nativeId ? header : null}
      {wide
        ? <Box flexDirection="row" gap={2}>{transcript}{controls}</Box>
        : <Box flexDirection="column" gap={1}>{controls}{transcript}</Box>}
    </Box>
  )
}

const HELP = [
  'Vexa in Claude — how it works',
  '1. /vexa opens the Vexa pane. Paste a meeting link → «Send Vexa», or «Try a demo». The live transcript appears in Meeting.',
  '2. «Listen» (pane or the band above the prompt): a small model reads every new piece of the transcript and wakes Claude only when the rules match. «Claude, …» in the call always reaches it.',
  '3. Ask Claude to tune it: «this is a standup, wake me only for blockers». Claude changes the rules with listener_configure.',
  '4. If the listener stays quiet while the call goes on, Claude is asked to check what it may have missed.',
  '5. Calendar: connect it and pick which meetings Vexa joins. Workspaces: pages Claude reads and writes; bind a meeting so its notes land there.',
].join('\n')

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'vexa', description: 'Vexa: your calls, the live listener, calendar and workspaces' })
    await $.command.register({ name: 'vexa-meeting', description: 'Open the Vexa pane at the meeting' })
    await $.command.register({ name: 'vexa-calls', description: 'Open the Vexa pane at your calls' })
    await $.command.register({ name: 'vexa-help', description: 'How the Vexa pane and listener work' })
    await $.command.register({ name: 'vexa-app-seed', description: 'Copy your Vexa app (pane copy, listener presets) into .vexa/app for this project' })
    await $.command.register({ name: 'vexa-app-reset', description: "Reset your Vexa app to the plugin's version (keeps a backup)" })
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
    await $.tool.register({
      name: 'vexa_settings',
      description: 'Read or save where Claude keeps what it learns from Vexa calls, exactly as the person answered in chat: '
        + 'write "cloud" (a Vexa workspace, by slug; also becomes the account\'s default write target, shared with the web), '
        + '"local" (markdown in a folder on this computer, default ~/Vexa), or "ask" (only when they ask). No arguments reads it.',
      inputSchema: { type: 'object', properties: {
        write: { enum: ['cloud', 'local', 'ask'] },
        workspace: { type: 'string', description: 'For cloud: the workspace slug (workspace_list).' },
        local_path: { type: 'string', description: 'For local: the folder; default ~/Vexa.' },
      } },
    })
    try {
      const saved = await $.store.get('settings')
      if (saved && typeof saved === 'object') settings = { ...NO_SETTINGS, ...(saved as Settings) }
    } catch { /* no saved choice: the context asks for one */ }
    await loadApp($)
    void $.ui.open({ id: PANE, title: app.pane.title })
    await connect($)
    if (!polling) {
      polling = true
      $.clock.every(1500, () => quiet((async () => { await poll($); await findLive($) })()))
      // Signing in happens in /mcp, outside the pane: notice it without a button press.
      $.clock.every(5000, async () => {
        try { if ((await read($)).connection !== 'ready') await connect($) } catch { /* the next tick tries again */ }
      })
      $.clock.every(30_000, () => quiet(audit($)))
      $.clock.every(60_000, () => quiet(loadCalls($)))
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
    await go($, 'home')
    return { text: 'Vexa opened.' }
  })
  on('command.run', { command: 'vexa-meeting' }, async $ => {
    await go($, 'meeting')
    return { text: 'Vexa meeting opened.' }
  })
  on('command.run', { command: 'vexa-calls' }, async $ => {
    await go($, 'calls')
    return { text: 'Vexa calls opened.' }
  })
  on('command.run', { command: 'vexa-help' }, async () => ({ text: HELP }))
  on('command.run', { command: 'vexa-app-seed' }, async $ => ({ text: await seedProject($) }))
  on('command.run', { command: 'vexa-app-reset' }, async $ => ({ text: await resetApp($) }))

  on('tool.call', { tool: 'mcp__vexa__vexa_settings' }, async ($, e) => ({ result: await saveSettings($, e) }))
    .catch(($, e, next) => (next.called ? next(e) : { deny: 'Saving the Vexa setting failed; try again.' }))

  on('prompt.compose', async ($, e, next) => {
    const r = await next(e)
    return { ...r, sections: [...r.sections, { id: 'vexa:context', text: contextText(await read($)), scope: 'session' }] }
  }).catch(($, e, next) => next(e))

  on('tool.call', { tool: 'mcp__vexa__listener_configure' }, async ($, e) => ({ result: await configure($, e) }))
    .catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener control failed; try again.' }))

  on('tool.call', { tool: 'mcp__vexa__listener_calibrate' }, async ($, e) => {
    const input = e as any
    const l = await readListener($)
    const rules = typeof input.rules === 'string' && input.rules.trim() ? input.rules.trim() : rulesText(l.brief)
    return { result: await calibrate($, rules, typeof input.fixture === 'string' ? input.fixture : '') }
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener calibration failed.' }))

  // The pane's own calls are the person's button presses: approved. A model call is approved
  // only for a read-only tool or the listener's own controls; speaking and leaving always ask the
  // person; the rest goes to the session's decider as it would without us.
  on('tool.check', async ($, e, next) => {
    if (e.tool === CONFIGURE_TOOL || e.tool === CALIBRATE_TOOL || e.tool === SETTINGS_TOOL) return { decision: 'allow', reason: "Vexa plugin's own control" }
    if (!e.tool.startsWith(prefix)) return next(e)
    if (next.origin.plugin === $.plugin.name) return { decision: 'allow', reason: 'Vexa pane button' }
    const name = e.tool.slice(prefix.length)
    if (READ_ONLY.has(name)) return { decision: 'allow', reason: 'Vexa read-only tool' }
    if (ASK_PERSON[name]) return { decision: 'ask', reason: ASK_PERSON[name] }
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
    const line = !l.isOn ? `${l.offer?.title ?? m.title ?? m.nativeId} is live · Claude is not listening`
      : f.stage === 'answering' ? 'Claude is answering ↓'
        : hot ? `Heard «${f.line.slice(-80)}» → Claude`
          : 'Listening'
    return (
      <Box flexDirection="row" gap={1} alignItems="center">
        {pulse(ui, 'band-dot', l.isOn)}
        <Box flexGrow={1} flexShrink={1} minWidth={0}>
          <Text color={!l.isOn ? 'inactive' : undefined} weight={hot ? 'semibold' : undefined} wrap="truncate-end">{line}</Text>
        </Box>
        {l.isOn ? <Button key="missed" label="What did I miss?" onPress={() => whatDidIMiss($)} /> : null}
        {l.isOn
          ? <Button key="listen-stop" label="Stop" onPress={() => stopListening($)} />
          : <Button key="listen" label="Listen" hotkey="l" variant="primary" onPress={() => listen($)} />}
        {!l.isOn ? <Button key="decline" plain onPress={() => decline($)}><Text color="inactive">No</Text></Button> : null}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const ui = $.ui.resolve(e) as any
    const { Box, Text, Button } = ui
    const m = await read($)
    const l = await readListener($)
    const title = l.app.pane.title

    if (m.connection === 'connecting') {
      return <Box flexDirection="column"><Text bold>{title}</Text><Text dimColor>Connecting…</Text></Box>
    }

    // Setup happens in the chat (founder, 24 Oct): the pane only says where things stand.
    if (m.connection !== 'ready') {
      return (
        <Box flexDirection="column">
          <Text bold>{title}</Text>
          <Text color="inactive">{m.connection === 'signin' ? 'Not signed in. Ask Claude to set up Vexa.' : `Not connected${m.error ? `: ${m.error.split('\n')[0]}` : '.'}`}</Text>
        </Box>
      )
    }

    const v = (await readView($)).name
    const rows = e.viewport?.rows ?? 30
    const cols = (e as any).props?.bodyColumns ?? 60
    const body = v === 'calls' ? await callsView($, ui)
      : v === 'meeting' ? await meetingView($, ui, rows, cols)
        : v === 'calendar' ? await calendarView($, ui)
          : v === 'workspaces' ? await workspacesView($, ui)
            : v === 'space' ? await spaceView($, ui)
              : await homeView($, ui)
    return (
      <Box flexDirection="column" gap={1}>
        {nav($, ui, v, m.offers)}
        {body}
      </Box>
    )
  })
}
