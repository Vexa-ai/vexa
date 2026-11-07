"use client";
/** APPROVE A HELD WRITE WITH A CLICK, NOT A TYPED CODE (founder 2026-10-16), drawn as THE inline card
 *  (UI 7g). A session that read the person's private sources is held from writing into a workspace
 *  other people read (`core/agent/control_plane/private_taint.py`). The card says what would be
 *  saved, where, who reads it, and which private sources are in it. "Save to <workspace>" sends the
 *  PERSON's own chat message carrying the confirmation, from their signed-in session to the chat
 *  route — which refuses every delegated (agent) caller, so the agent can never approve itself. The
 *  typed code stays the fallback where there is no UI. */
import { ASK_CHAT_EVENT } from "../platform/events";
import { InlineCard, type CardReply } from "../ui-kit/InlineCard";

export type PrivateApproval = { code: string; workspace: string; name?: string; path: string; readers: string[]; sources: string[] };

/** A held write (`private_sources_to_shared`) in a tool result, or null. */
export function privateApproval(result: unknown): PrivateApproval | null {
  let r: unknown = result;
  if (typeof r === "string") {
    const at = r.indexOf("{");
    try { r = at >= 0 ? JSON.parse(r.slice(at)) : null; } catch { r = null; }
  }
  if (r && typeof r === "object" && "detail" in (r as Record<string, unknown>) && typeof (r as Record<string, unknown>).detail === "object") {
    r = (r as Record<string, unknown>).detail;
  }
  const o = (r ?? {}) as Record<string, unknown>;
  if (o.refused !== "private_sources_to_shared" || typeof o.confirm_code !== "string") return null;
  const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : []);
  return { code: o.confirm_code, workspace: String(o.workspace ?? ""), ...(typeof o.workspace_name === "string" && o.workspace_name ? { name: o.workspace_name } : {}),
           path: String(o.path ?? ""), readers: list(o.readers), sources: list(o.sources) };
}

/** the reply IS the person's message: `confirm <code>` is what the chat route attests */
const say = (_id: string, r: CardReply | string) => {
  const label = typeof r === "string" ? r : r.label;
  const prompt = typeof r === "string" ? r : r.reply ?? r.label;
  window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt, display: label } }));
};

export function ApprovalCard({ a, workspaceName }: { a: PrivateApproval; workspaceName: string }) {
  const where = workspaceName || a.workspace;
  const who = a.readers.slice(0, 5).join(", ") + (a.readers.length > 5 ? ` and ${a.readers.length - 5} more` : "");
  return <InlineCard onAnswer={say} spec={{
    id: `approve-${a.code}`,
    question: `Save ${a.path} to ${where}?`,
    summary: [
      ...(who ? [["Readable by", who] as [string, string]] : []),
      ...(a.sources.length ? [["Includes", `details from your private ${a.sources.join(", ")}`] as [string, string]] : []),
    ],
    primary: { label: `Save to ${where}`, reply: `confirm ${a.code}` },
    secondary: { label: "Keep private", reply: `Keep it private: do not write ${a.path} to ${where}. Leave out the private details.` },
  }} />;
}
