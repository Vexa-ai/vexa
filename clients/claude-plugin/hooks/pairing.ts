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
