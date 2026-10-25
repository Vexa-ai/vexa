// One-chat install: the plugin pairs this computer with the person's Vexa (a code they carry from
// the connect page or that the plugin asks for), and once they sign in, reaches Vexa's tools with
// the key the pairing hands over — over HTTP, as tools of its own. A Vexa server added any other
// way would only show its tools in the next chat; these exist in this one.

export const VEXA_API = 'https://claude-rc.dev.vexa.ai'
export const PAIR_CODE = /\bVX-[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}\b/

// A streamable-HTTP MCP answer is plain JSON or server-sent events; the JSON-RPC message either way.
export function rpcMessage(text: string): any {
  const t = String(text ?? '').trim()
  if (t.startsWith('{')) return JSON.parse(t)
  const data = t.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trim()).filter(Boolean)
  for (let i = data.length - 1; i >= 0; i--) {
    try { const m = JSON.parse(data[i]!); if (m && (m.result !== undefined || m.error !== undefined)) return m } catch { /* next */ }
  }
  throw new Error('no JSON-RPC answer in the response')
}

// What a tool call answers, as text for the model: the content blocks' text, joined.
export function resultText(result: any): string {
  const blocks: any[] = result?.content ?? []
  const text = blocks.filter(b => b?.type === 'text').map(b => String(b.text ?? '')).join('\n')
  return text || JSON.stringify(result ?? {})
}

// The tools Vexa offers that the plugin forwards: everything but its own, so names never collide.
export function forwardable(tools: any[], own: string[]): { name: string; description: string; inputSchema: any }[] {
  return (tools ?? []).filter(t => t?.name && !own.includes(t.name)).map(t => ({
    name: String(t.name), description: String(t.description ?? '').slice(0, 2000),
    inputSchema: t.inputSchema && typeof t.inputSchema === 'object' ? t.inputSchema : { type: 'object', properties: {} },
  }))
}

// What a call's lines bring to the record: names not heard before (people, companies, projects),
// and decision or commitment lines. Word rules, English and Russian; no model.
const NAME = /(?<=[\p{L},;:] )(\p{Lu}[\p{L}'’-]{2,}(?: \p{Lu}[\p{L}'’-]{2,})?)/gu
const NOT_NAMES = new Set(['claude', 'клод', 'vexa', 'google', 'google meet', 'zoom', 'teams', 'microsoft teams', 'okay', 'monday', 'tuesday', 'wednesday',
  'thursday', 'friday', 'saturday', 'sunday', 'january', 'february', 'march', 'april', 'june', 'july', 'august', 'september', 'october', 'november', 'december'])
const DECISION = /\b(?:decided|agreed|let's go with|we(?:'ll| will) go with|approved|settled on|i(?:'ll| will) (?:send|share|do|prepare|write|draft|check|follow|book|call|email|review|fix|ship|own)|by (?:monday|tuesday|wednesday|thursday|friday|tomorrow|end of (?:the )?(?:day|week|month)))\b|(?:договорились|решили|решено|согласовали|утвердили|беру на себя|сделаю|пришлю|отправлю|подготовлю)/iu

export function recordable(lines: { speaker: string; text: string }[], known: Set<string>): { names: string[]; decisions: string[] } {
  const names: string[] = []
  const decisions: string[] = []
  for (const l of lines) {
    const speaker = String(l.speaker ?? '').trim()
    const candidates = [...String(l.text ?? '').matchAll(NAME)].map(m => m[1]!)
    if (speaker && !/^(you|speaker\b|unknown|\?|—)/i.test(speaker)) candidates.push(speaker)
    for (const c of candidates) {
      const k = c.toLowerCase()
      if (NOT_NAMES.has(k) || known.has(k)) continue
      known.add(k)
      names.push(c)
    }
    if (DECISION.test(String(l.text ?? ''))) decisions.push(`${speaker || '?'}: ${String(l.text).trim().slice(0, 160)}`)
  }
  return { names, decisions }
}
