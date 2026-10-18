/** A CHAT'S SCOPE CHANGED (Vexa-ai/vexa#1783): where it writes, what it reads, or which workspaces
 *  are hidden. The header's scope line re-reads the server when this fires, so a change the agent
 *  made in words shows up in the header the moment its tool answers — never a client-side guess. */
export const SCOPE_CHANGED = "vexa:workspace-scope";

/** The verbs whose success changes a scope. A closed list, for the reason the harness's are. */
const SCOPE_TOOL = /(^|__)(workspace_(state_set|defaults_set|remove|target|new|create|invite_link_create|import|import_status)|chat_profile_(set|edit|delete))$/;

export const isScopeTool = (tool: string) => SCOPE_TOOL.test(tool);

export function notifyScope(): void {
  try { window.dispatchEvent(new Event(SCOPE_CHANGED)); } catch { /* no window (tests, SSR) */ }
}
