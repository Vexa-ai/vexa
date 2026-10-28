/** A Gmail connection's one line and its one quiet action (founder 2026-10-16): drafts come with the
 *  connection; a reconnect is offered only when the draft grant is missing. */
export function gmailView(c: { status: string; permissions?: string[] }): { line: string; action: "Reconnect" | null } {
  if (c.status !== "ready") return { line: "Not connected", action: null };
  const drafts = (c.permissions ?? []).includes("drafts");
  return drafts ? { line: "Connected · Drafts on", action: null } : { line: "Connected · Drafts off", action: "Reconnect" };
}
