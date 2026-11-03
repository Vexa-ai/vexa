/** MINUTES local-dev seam: record a meeting→group assignment.
 *
 *  POST { uid, workspaceId } appends one line to the bindings log. This is the DEV stand-in for
 *  the real binding store (P4 makes assignment trigger the group re-run); it exists so the
 *  organiser's "assign to a group" click completes a witnessable loop today. 404 outside
 *  local-dev minutes mode — never a production surface.
 */
import { appendFile } from "node:fs/promises";
import { NextResponse } from "next/server";

const BINDINGS = "/tmp/minutes-bindings.jsonl";

export async function POST(req: Request) {
  if (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_TERMINAL_MODE !== "minutes")
    return NextResponse.json({ error: "not found" }, { status: 404 });
  const { uid, workspaceId } = await req.json().catch(() => ({}));
  if (!uid || !workspaceId)
    return NextResponse.json({ error: "uid and workspaceId required" }, { status: 400 });
  await appendFile(BINDINGS, JSON.stringify({ uid, workspaceId, at: new Date().toISOString() }) + "\n");
  return NextResponse.json({ ok: true });
}
