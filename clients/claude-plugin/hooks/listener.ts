// The listener's pure parts: the listen brief (signals.v1 `Brief`, the rule model the app's chat
// uses too), the app folder its presets come from, rule triggers and the attention filter's prompt.
// Brief bounds follow core/signals brief.py, so a brief means the same thing in both places; the
// filter is the dogfood listener's calibrated one. No `$` here: register.tsx does the I/O.
import type { Brief, Label, Line, Rule } from '../types'

export const ADDRESSED = 'addressed_to_vexa'
export const ADDRESSED_AT = 0.5
export const MAX_LABELS = 12
export const MAX_RULES = 20
const NAME = /^[a-z][a-z0-9_]{0,39}$/
// A quantified group that itself holds a quantifier: catastrophic backtracking, refused.
const NESTED_QUANTIFIER = /\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]/

export const QUESTIONS: Record<string, string> = {
  [ADDRESSED]: 'Does NEW SPEECH address Vexa, the meeting assistant, directly: a question, request or '
    + 'instruction to it? Mentions of Vexa that are not addressed to it do not count.',
  decision: 'Does NEW SPEECH state a decision the group has made?',
  action_item: 'Does NEW SPEECH assign a task or a commitment to someone, with or without a date?',
  question_to_user: 'Does NEW SPEECH ask the person this assistant works for a question, or wait for their answer?',
  price: 'Does NEW SPEECH name a price, a budget, a discount or other money terms?',
  objection: 'Does NEW SPEECH raise a concern, an objection or a reason not to go ahead?',
  next_step: 'Does NEW SPEECH agree a next step, a follow-up or a date?',
  blocker: 'Does NEW SPEECH say someone is blocked or needs help?',
  red_flag: 'Does NEW SPEECH contain a claim that contradicts something said earlier, or a red flag about the speaker?',
}

// The app the listener and pane run from: a folder with a `vexa-app.json` manifest (see
// app/README.md). The plugin ships one; a project's own `.vexa/app/` replaces it.
export type PaneCopy = { title: string; emptyHint: string; demo: boolean; offerListening: boolean }
export type App = {
  name: string
  version: string
  source: string
  upstream: string
  defaultPreset: string
  presets: Record<string, Brief>
  pane: PaneCopy
}

export const PANE_DEFAULTS: PaneCopy = {
  title: 'Vexa',
  emptyHint: 'No meeting right now? Try a demo: a presenter speaks for a minute and you watch it transcribed live.',
  demo: true,
  offerListening: true,
}

export const DEFAULT_RULES = 'Wake when a speaker addresses the assistant (by name, or by a likely mis-hearing of it) '
  + 'WITH a request, question or instruction for it, not a bare mention or a remark like "the assistant is listening". '
  + 'Also wake when a speaker gives a short imperative to the assistant even without its name ("read the transcript", '
  + '"take it from there"), when someone says that was a question and expects an answer, and on decisions, prices, '
  + 'deadlines and commitments. Do not wake for musings, thinking aloud, complaints, filler, or talk ABOUT the assistant.'

// What runs when no app folder can be read: the default preset alone.
export const FALLBACK_APP: App = {
  name: 'built-in', version: '0', source: 'built-in', upstream: '', defaultPreset: 'default',
  presets: { default: normalize({ focus: DEFAULT_RULES }) },
  pane: PANE_DEFAULTS,
}

export type Manifest = {
  schema?: string
  name?: string
  version?: string
  upstream?: { repo?: string; path?: string; ref?: string }
  entry?: { pane?: string; listener?: string; skills?: string; flows?: string }
  listener?: { defaultPreset?: string }
}

export function parseManifest(text: string): Manifest | null {
  try {
    const m = JSON.parse(text)
    return m && typeof m === 'object' && m.schema === 'vexa-app/v1' ? m as Manifest : null
  } catch { return null }
}

// The app from its manifest, its pane copy and its listener files (`<preset>.json` → text).
export function buildApp(source: string, m: Manifest, paneText: string | null, briefs: Record<string, string>): App {
  const presets: Record<string, Brief> = {}
  for (const [file, text] of Object.entries(briefs)) {
    const name = file.replace(/\.json$/, '')
    if (!NAME.test(name)) continue
    try { presets[name] = normalize(JSON.parse(text)) } catch { /* a broken preset is skipped */ }
  }
  if (!Object.keys(presets).length) Object.assign(presets, FALLBACK_APP.presets)
  let pane = PANE_DEFAULTS
  try { if (paneText) pane = { ...PANE_DEFAULTS, ...JSON.parse(paneText) } } catch { /* defaults */ }
  const wanted = m.listener?.defaultPreset ?? 'default'
  const up = m.upstream
  return {
    name: String(m.name ?? 'app'), version: String(m.version ?? '0'), source,
    upstream: up?.repo ? `${up.repo}${up.path ? `/${up.path}` : ''}${up.ref ? `@${up.ref}` : ''}` : '',
    defaultPreset: presets[wanted] ? wanted : Object.keys(presets)[0]!,
    presets, pane,
  }
}

export function briefFor(app: App, preset: string): Brief {
  return app.presets[preset] ?? app.presets[app.defaultPreset] ?? FALLBACK_APP.presets.default!
}

function bounded(v: unknown, fallback: number, lo: number, hi: number): number {
  const n = typeof v === 'number' ? v : Number.NaN
  return Number.isFinite(n) && n >= lo && n <= hi ? n : fallback
}

// What brief.py enforces: valid label names, at most 12, `addressed_to_vexa` always present and
// waking; wake_at and max_wait_s inside their bounds; rules valid and at most 20.
export function normalize(raw: Partial<Brief>): Brief {
  const labels: Label[] = []
  for (const l of raw.labels ?? []) {
    if (!l || !NAME.test(l.name) || labels.some(x => x.name === l.name) || l.name === ADDRESSED) continue
    labels.push({ name: l.name, wake: l.wake !== false, ...(l.question ? { question: String(l.question).slice(0, 2000) } : {}) })
  }
  return {
    labels: [{ name: ADDRESSED, wake: true }, ...labels].slice(0, MAX_LABELS),
    focus: String(raw.focus ?? '').slice(0, 2000),
    known: String(raw.known ?? '').slice(0, 8000),
    wake_at: bounded(raw.wake_at, 1.0, 0.05, 100),
    max_wait_s: bounded(raw.max_wait_s, 3600, 10, 3600),
    rules: validRules(raw.rules ?? []),
  }
}

export function validRules(rules: readonly Rule[]): Rule[] {
  return rules.filter(r => r && ['keyword', 'person', 'regex'].includes(r.kind)
    && typeof r.value === 'string' && r.value.length >= 1 && r.value.length <= 200
    && (r.kind !== 'regex' || isSafeRegex(r.value)))
    .map(r => ({ kind: r.kind, value: r.value, wake: r.wake !== false }))
    .slice(0, MAX_RULES)
}

function isSafeRegex(v: string): boolean {
  if (NESTED_QUANTIFIER.test(v)) return false
  try { new RegExp(v, 'i'); return true } catch { return false }
}

// A rule trigger wakes at once, with no model call (core.py: a match counts as addressed).
export function ruleHit(rules: readonly Rule[], lines: readonly Line[]): string | null {
  for (const r of rules) {
    if (!r.wake) continue
    for (const l of lines) {
      const hit = r.kind === 'person' ? l.speaker.toLowerCase().includes(r.value.toLowerCase())
        : r.kind === 'keyword' ? l.text.toLowerCase().includes(r.value.toLowerCase())
          : new RegExp(r.value, 'i').test(l.text)
      if (hit) return `rule ${r.kind} "${r.value}"`
    }
  }
  return null
}

// The attention filter: one small-model call per change in the transcript tail, one letter back
// (W wake, S skip). This is the dogfood listener's prompt: instructions and rules lead and are
// cached, so each call sends only the few transcript lines. `listener_calibrate` replays a
// labelled fixture through it.
export const DECIDE_SYSTEM = 'You are an attention filter for an assistant listening to a live meeting transcript '
  + '(any language, with speech-recognition errors). Decide whether the NEW lines need the assistant now. Answer '
  + 'with exactly one letter: W = wake the assistant, S = skip. No other text.'

// The brief as the filter reads it: its focus is the plain-language rules; label questions and
// what is already known are added to them.
export function rulesText(brief: Brief): string {
  const asks = brief.labels.filter(l => l.wake && l.name !== ADDRESSED)
    .map(l => l.question || QUESTIONS[l.name] || l.name.replace(/_/g, ' '))
  return [
    brief.focus || 'Wake when the speaker addresses the assistant with a request, question or instruction.',
    asks.length ? `Also wake when: ${asks.join(' ')}` : '',
    brief.known ? `Ignore, already known or not wanted: ${brief.known}` : '',
  ].filter(Boolean).join('\n')
}

export function decidePrompt(context: readonly string[], fresh: readonly string[]): string {
  return `Context:\n${context.join('\n') || '-'}\nNEW:\n${fresh.join('\n')}`
}

export function parseDecision(text: string): { wake: boolean; reason: string } {
  const wake = text.trim().toUpperCase().startsWith('W')
  return { wake, reason: wake ? 'matched the rules' : 'no match' }
}

// One wake per thought: the same words (a draft and its final version, a repeat) wake once.
export const norm = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()

// Addressing the assistant by name (with the usual mis-hearings) lets a wake through the gap,
// and the speaker's next lines within FOLLOW_MS are the rest of the request.
const DIRECT = /(^|[\s:,])(claude|clod|vexa|векса|клод|плод|клот|ход)[,!.?\s]/i
export function isDirect(lines: readonly string[]): boolean {
  return DIRECT.test(` ${lines.join(' ')} `)
}

export type Labelled = { speaker: string; text: string; wake: boolean }
export type Row = { text: string; expected: boolean; got: boolean; reason: string; ms: number }

export function calibrationSummary(rows: readonly Row[]) {
  const tp = rows.filter(r => r.expected && r.got).length
  const fp = rows.filter(r => !r.expected && r.got).length
  const fn = rows.filter(r => r.expected && !r.got).length
  const ms = rows.map(r => r.ms).sort((a, b) => a - b)
  return {
    lines: rows.length, should_wake: tp + fn, tp, fp, fn,
    precision: +(tp / Math.max(1, tp + fp)).toFixed(2), recall: +(tp / Math.max(1, tp + fn)).toFixed(2),
    ms_p50: ms[Math.floor(ms.length / 2)] ?? 0, ms_p95: ms[Math.floor(ms.length * 0.95)] ?? 0, ms_max: ms[ms.length - 1] ?? 0,
  }
}
