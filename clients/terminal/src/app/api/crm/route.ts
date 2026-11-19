/** The CRM record page's proxy (ADR-0047), through the one edge (ADR-0036).
 *
 *  Forwards the signed-in person's OWN credential (the session cookie) — never a deployment key — to
 *  the gateway's `/crm/<verb>`, which resolves it like every other key and forwards it to crm-api.
 *  Only the three verbs the record page needs exist at the edge: read, history and review. Everything
 *  else about the CRM is the agent's (MCP first). A deployment without the CRM answers 404 at the
 *  edge, and the page says so. A cross-origin browser POST is refused. */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "../auth/adminApi";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
// + the public-profile link on a contact (R4, `public_profiles`): read it live, link or unlink
const CRM_OPERATIONS = new Set(["read", "history", "review", "profile/read", "profile/link"]);

export async function POST(req: Request): Promise<Response> {
  let token: string | undefined;
  try { token = (await cookies()).get(AUTH_COOKIE)?.value; } catch { token = undefined; }
  if (!token) return Response.json({ detail: "Sign in to use the CRM" }, { status: 401 });
  const origin = req.headers.get("origin");
  if (origin) {
    const host = req.headers.get("host") || new URL(req.url).host;
    let originHost = "";
    try { originHost = new URL(origin).host; } catch { originHost = ""; }
    if (originHost !== host) return Response.json({ detail: "Invalid request origin" }, { status: 403 });
  }
  let body: unknown;
  try { body = await req.json(); } catch { return Response.json({ detail: "Invalid JSON" }, { status: 400 }); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return Response.json({ detail: "Expected an object" }, { status: 400 });
  const { operation, ...args } = body as Record<string, unknown>;
  if (typeof operation !== "string" || !CRM_OPERATIONS.has(operation)) {
    return Response.json({ detail: "Unknown CRM operation" }, { status: 400 });
  }
  try {
    const upstream = await fetch(`${GATEWAY_URL}/crm/${operation}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": token },
      body: JSON.stringify(args), cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new Response(await upstream.text(), {
      status: upstream.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "The CRM is temporarily unavailable" }, { status: 503 });
  }
}
