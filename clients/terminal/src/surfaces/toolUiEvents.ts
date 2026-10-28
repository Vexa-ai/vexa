/** Tools whose call asks THIS TAB to open something the agent cannot reach itself. The agent has
 *  no browser and no microphone: `connection_request` (consent is a human click in the Connections
 *  panel) and `record_voice_note` (the person speaks) each answer with a `ui_action`, and the chat
 *  forwards the call as a window event the shell owns. This surface opens nothing itself; the event
 *  names are the shell's (`minutes/connectionEvents.ts`, `minutes/voiceNote.ts`), restated here
 *  because surfaces sit below minutes, and pinned equal by `minutes/__tests__/voiceNoteEntry.test.ts`. */
const TOOL_UI_EVENTS: ReadonlyArray<[RegExp, string]> = [
  [/(^|__)connection_request$/, "vexa:connections-open"],
  [/(^|__)record_voice_note$/, "vexa:voice-note-open"],
];

/** The window event a tool call raises in this tab, or null. MCP prefixes (`mcp__vexa__…`) match. */
export function toolUiEvent(tool: string): string | null {
  for (const [re, name] of TOOL_UI_EVENTS) if (re.test(tool)) return name;
  return null;
}

/** Tools whose RESULT asks this tab to open something: `browser_session_open` answers with
 *  `ui_action: "open_browser_session"` and the session, and the shell's live sign-in view opens on
 *  `vexa:browser-session-open` (`minutes/browserSession.ts` BROWSER_SESSION_OPEN, restated here for
 *  the same reason as above, pinned equal by `minutes/__tests__/browserSessionView.test.tsx`). */
const TOOL_RESULT_UI_EVENTS: ReadonlyArray<[RegExp, string, string]> = [
  [/(^|__)browser_session_open$/, "open_browser_session", "vexa:browser-session-open"],
];

/** The window event (and its detail, the result's body) a successful tool result raises in this
 *  tab, or null. The result may arrive as an object or as its JSON text. */
export function toolResultUiEvent(tool: string, result: unknown): { name: string; detail: Record<string, unknown> } | null {
  const row = TOOL_RESULT_UI_EVENTS.find(([re]) => re.test(tool));
  if (!row) return null;
  let body = result;
  if (typeof body === "string") {
    try { body = JSON.parse(body); } catch { return null; }
  }
  if (!body || typeof body !== "object" || (body as Record<string, unknown>).ui_action !== row[1]) return null;
  return { name: row[2], detail: body as Record<string, unknown> };
}
