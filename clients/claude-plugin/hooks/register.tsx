// Vexa pane: opens beside the chat at session start and drives the plugin's own `vexa` MCP server.
// Not signed in → a «Sign in» state; signed in → paste a link or try the demo, then the live
// transcript follows, drafts in place until they are final.
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

import type { AppInfo, Listener, Meeting, Room, Seg } from '../types'
import {
  DECIDE_SYSTEM, FALLBACK_APP, briefFor, buildApp, calibrationSummary, decidePrompt, isDirect,
  normalize, parseDecision, parseManifest, ruleHit, rulesText,
} from './listener'
import type { App, Labelled, Row } from './listener'

const PANE = 'vexa-meeting'
const SERVER_KEY = 'vexa'
const EMPTY: Meeting = {
  connection: 'connecting', url: '', platform: '', nativeId: '', title: '', isDemo: false,
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
        time: ev.tsMs ? new Date(ev.tsMs).toISOString().slice(11, 16) : '', pending: ev.completed === false,
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
    time: String(s.absolute_start_time ?? s.start_time ?? '').slice(11, 16),
    pending: s.completed === false,
  })).filter(s => s.text)
  // Ask again from the first draft next time, so its final version replaces it.
  const next = firstPending >= 0 ? base + firstPending : (typeof page.next_index === 'number' ? page.next_index : since + segs.length)
  const now = String(page.status ?? status)
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
  if (m.connection !== 'ready' || !isLive(m) || pollingNow) return
  pollingNow = true
  try {
    let status = m.status
    if (!m.liveFeed) {
      status = await readConfirmed($, m.nextIndex, status)
    } else {
      const p = await call($, 'get_meeting_transcript', {
        platform: m.platform, native_meeting_id: m.nativeId, limit: 1, include_pending: true, live_wait_ms: LIVE_WAIT_MS,
        ...(m.liveCursor ? { live_cursor: m.liveCursor } : {}),
      })
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
    if (['completed', 'failed', 'stopped'].includes(status)) await updateListener($, x => ({ ...x, isOn: false }))
  } catch (err) {
    await update($, x => ({ ...x, error: message(err) }))
  } finally {
    pollingNow = false
  }
}

async function start($: any, tool: string, args: Record<string, unknown>, isDemo: boolean): Promise<void> {
  await update($, x => ({ ...x, isDemo, title: '', status: 'requesting', error: '', segments: [], nextIndex: 0, liveCursor: '', liveFeed: true, nativeId: '' }))
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

const roomKey = (r: Room) => `${r.platform}/${r.nativeId}`

// A live meeting of the person's, not yet offered or declined, becomes the band's offer.
let lastOfferCheck = 0
async function findLive($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  const now = await $.clock.now()
  if (!l.app.pane.offerListening || l.isOn || l.offer || now - lastOfferCheck < OFFER_EVERY_MS) return
  lastOfferCheck = now
  if (m.nativeId && m.status === 'active' && !l.declined.includes(roomKey({ ...m, title: '' }))) {
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
  await updateListener($, x => ({ ...x, offer: null, isOn: true, triaged: heard.slice(-500), wakes: [], lastWakeAt: 0, error: '' }))
  void $.prompt.submit({
    text: `[Vexa listener] The person pressed Listen on "${room.title}" (${room.platform} ${room.nativeId}). `
      + `I watch its transcript and wake you when something matters under the "${l.preset}" listen brief. `
      + `If you can tell what kind of conversation this is, tune it with ${CONFIGURE}; otherwise acknowledge in one line.`,
  })
}

async function decline($: any): Promise<void> {
  await updateListener($, x => (x.offer ? { ...x, declined: [...x.declined, roomKey(x.offer)].slice(-50), offer: null } : x))
}

async function stopListening($: any): Promise<void> {
  const m = await read($)
  await updateListener($, x => ({ ...x, isOn: false, declined: m.nativeId ? [...x.declined, roomKey({ ...m, title: '' })].slice(-50) : x.declined }))
}

async function decide($: any, rules: string, context: string[], fresh: string[]): Promise<{ wake: boolean; reason: string; ms: number }> {
  const t0 = await $.clock.now()
  const r = await $.model.complete({
    model: FILTER_MODEL,
    maxTokens: 80,
    system: [{ text: DECIDE_SYSTEM, cache: true }, { text: `Rules: ${rules}` }],
    prompt: decidePrompt(context, fresh),
  })
  const ms = (await $.clock.now()) - t0
  if (!r.isAnswered) return { wake: false, reason: `no answer (${r.reason})`, ms }
  return { ...parseDecision(r.text), ms }
}

// Judges the tail of the transcript, drafts included, whenever it changes. A rule trigger wakes
// with no model call; a segment that already woke the main Claude cannot wake it again; wakes are
// WAKE_GAP_MS apart unless the speaker addressed the assistant, whose next lines within
// FOLLOW_MS are the rest of the request.
const WOKE_FOR = new Set<string>()
let lastTail = ''
let lastDirectAt = 0
let triaging = false
async function triage($: any): Promise<void> {
  const l = await readListener($)
  const m = await read($)
  if (!l.isOn || triaging) return
  const tail = m.segments.slice(-WINDOW)
  const tailText = tail.map(s => `${s.speaker}: ${s.text}`).join('\n')
  if (!tail.length || tailText === lastTail) return
  lastTail = tailText
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
    const direct = isDirect(lines)
    const followUp = lastDirectAt > 0 && now - lastDirectAt < FOLLOW_MS
    if (direct && v.wake) lastDirectAt = now
    const gapOk = !l.lastWakeAt || now - l.lastWakeAt > WAKE_GAP_MS
    const canWake = !ids.some(id => WOKE_FOR.has(id)) && l.wakes.length < MAX_WAKES
      && ((v.wake && (direct || Boolean(hit) || gapOk)) || followUp)
    if (canWake) ids.forEach(id => WOKE_FOR.add(id))
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
      void $.prompt.submit({
        text: `[Vexa listener] ${v.reason}\n\nFrom the live meeting "${m.title || m.nativeId}" (${m.platform} ${m.nativeId}):\n`
          + `${[...context.slice(-2), ...lines].join('\n')}\n\n`
          + `React briefly if it matters to the person; tune the listener with ${CONFIGURE} if it woke you for nothing.`,
      })
    }
  } catch (err) {
    await updateListener($, x => ({ ...x, error: message(err) }))
  } finally {
    triaging = false
  }
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
  const misses = rows.filter(r => r.expected !== r.got).map(r => `${r.got ? 'FALSE WAKE' : 'MISSED'}: «${r.text}» — ${r.reason}`)
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

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'vexa', description: 'Open the Vexa meeting pane beside the chat' })
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
    await loadApp($)
    void $.ui.open({ id: PANE, title: app.pane.title })
    void connect($)
    if (!polling) {
      polling = true
      $.clock.every(1500, async () => { await poll($); await findLive($) })
    }
    return next(e)
  })

  on('command.run', { command: 'vexa' }, async $ => {
    await $.ui.open({ id: PANE, title: app.pane.title })
    await connect($)
    return { text: 'Vexa pane opened.' }
  })

  on('command.run', { command: 'vexa-app-seed' }, async $ => ({ text: await seedProject($) }))
  on('command.run', { command: 'vexa-app-reset' }, async $ => ({ text: await resetApp($) }))

  on('tool.call', { tool: 'mcp__vexa__listener_configure' }, async ($, e) => ({ result: await configure($, e) }))
    .catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener control failed; try again.' }))

  on('tool.call', { tool: 'mcp__vexa__listener_calibrate' }, async ($, e) => {
    const input = e as any
    const l = await readListener($)
    const rules = typeof input.rules === 'string' && input.rules.trim() ? input.rules.trim() : rulesText(l.brief)
    return { result: await calibrate($, rules, typeof input.fixture === 'string' ? input.fixture : '') }
  }).catch(($, e, next) => (next.called ? next(e) : { deny: 'The Vexa listener calibration failed.' }))

  // The pane's own calls are the person's button presses: approved. A model call is approved
  // only for a read-only tool or the listener's own controls; the rest goes to the session's
  // decider as it would without us.
  on('tool.check', async ($, e, next) => {
    if (e.tool === CONFIGURE_TOOL || e.tool === CALIBRATE_TOOL) return { decision: 'allow', reason: "Vexa listener's own control" }
    if (!e.tool.startsWith(prefix)) return next(e)
    if (next.origin.plugin === $.plugin.name) return { decision: 'allow', reason: 'Vexa pane button' }
    if (READ_ONLY.has(e.tool.slice(prefix.length))) return { decision: 'allow', reason: 'Vexa read-only tool' }
    return next(e)
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const l = await readListener($)
    if (!l.offer || l.isOn || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="row" gap={1}>
        <Text>{`Vexa: "${l.offer.title}" is live. Listen and tell you when something matters?`}</Text>
        <Button key="listen" label="Listen" variant="primary" onPress={() => listen($)} />
        <Button key="decline" label="No" onPress={() => decline($)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Input } = $.ui.resolve(e) as any
    const m = await read($)
    const l = await readListener($)
    const title = l.app.pane.title

    if (m.connection === 'connecting') {
      return <Box flexDirection="column"><Text bold>{title}</Text><Text dimColor>Connecting…</Text></Box>
    }

    if (m.connection !== 'ready') {
      return (
        <Box flexDirection="column">
          <Text bold>{title}</Text>
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
    const room = Math.max(4, Math.floor(((e.viewport?.rows ?? 30) - (l.isOn ? 16 : 10)) / 2))
    return (
      <Box flexDirection="column">
        <Text bold>{title}</Text>
        {m.nativeId ? <Text dimColor>{`${m.isDemo ? 'demo · ' : ''}${m.title || `${m.platform} ${m.nativeId}`} · ${m.status}`}</Text> : null}
        {l.isOn ? (
          <Box key="listener" flexDirection="column">
            <Text>{`Listening · ${l.preset} · woke Claude ${l.wakes.length}×`}</Text>
            <Text dimColor>{`Rules: ${rulesText(l.brief).slice(0, 240)}`}</Text>
            {l.brief.rules.length ? <Text dimColor>{`Triggers: ${l.brief.rules.map(r => `${r.kind} "${r.value}"`).join(', ')}`}</Text> : null}
            {l.lastVerdict ? <Text dimColor>{`Last: ${l.lastVerdict} (${l.lastMs} ms)`}</Text> : null}
            {l.error ? <Text color="red">{l.error}</Text> : null}
            <Button key="unlisten" label="Stop listening" onPress={() => stopListening($)} />
          </Box>
        ) : null}
        {!live ? <Input key="url" label="Meeting link" placeholder="https://meet.google.com/abc-defg-hij" value={m.url} submitLabel="Send bot" onInput={(v: string) => update($, x => ({ ...x, url: v.trim() }))} onSubmit={(v: string) => send($, v)} /> : null}
        <Box flexDirection="row" gap={1}>
          {!live ? <Button key="send" label="Send bot" variant="primary" onPress={() => send($, m.url)} /> : null}
          {!live && l.app.pane.demo ? <Button key="demo" label="Try a demo" onPress={() => start($, 'start_demo_meeting', {}, true)} /> : null}
          {live && !l.isOn && m.status === 'active' ? <Button key="listen-pane" label="Listen" onPress={async () => { await updateListener($, x => ({ ...x, offer: { platform: m.platform, nativeId: m.nativeId, title: m.title || m.nativeId } })); await listen($) }} /> : null}
          {live ? <Button key="stop" label="Stop bot" onPress={() => stop($)} /> : null}
          {!live && m.segments.length ? <Button key="clear" label="Clear" onPress={() => update($, x => ({ ...EMPTY, connection: x.connection }))} /> : null}
        </Box>
        {m.error ? <Text color="red">{m.error.split('\n')[0]}</Text> : null}
        {m.segments.length === 0 ? (
          <Text dimColor>{live
            ? (m.isDemo ? 'The demo presenter starts speaking in a few seconds…' : 'Waiting for the bot to be admitted and for speech…')
            : l.app.pane.emptyHint}</Text>
        ) : null}
        {runs(m.segments).slice(-room).map((s, i) => (
          <Box key={`seg-${i}`} flexDirection="column">
            <Text bold>{`${s.speaker}${s.time ? ` · ${s.time}` : ''}`}</Text>
            {s.text ? <Text>{s.text}</Text> : null}
            {s.draft ? <Text dimColor italic>{s.draft}</Text> : null}
          </Box>
        ))}
      </Box>
    )
  })
}
