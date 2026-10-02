/** Optional CRM proxy. Forward the signed-in person's credential; never a deployment key. */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "../auth/adminApi";
export const dynamic = "force-dynamic";
const operations = new Set(["describe", "search", "read", "change", "history", "review", "configure"]);
export async function POST(req: Request) {
  const base = process.env.CRM_API_URL;
  if (!base) return Response.json({ detail: "CRM is not enabled" }, { status: 404 });
  const token = (await cookies()).get(AUTH_COOKIE)?.value;
  if (!token) return Response.json({ detail: "Sign in to use CRM" }, { status: 401 });
  const origin = req.headers.get("origin");
  if (origin && (new URL(origin).host !== (req.headers.get("host") || new URL(req.url).host))) return Response.json({ detail: "Invalid request origin" }, { status: 403 });
  let body;
  try { body = await req.json(); } catch { return Response.json({ detail: "Invalid JSON" }, { status: 400 }); }
  if (!body || typeof body !== "object" || Array.isArray(body)) return Response.json({ detail: "Expected an object" }, { status: 400 });
  const { operation, ...arguments_ } = body;
  if (!operations.has(operation)) return Response.json({ detail: "Unknown CRM operation" }, { status: 400 });
  try {
    const response = await fetch(`${base.replace(/\/$/, "")}/${operation}`, {
      method: "POST", headers: { "Content-Type": "application/json", "X-API-Key": token },
      body: JSON.stringify(arguments_), cache: "no-store", signal: AbortSignal.timeout(15000),
    });
    return new Response(await response.text(), { status: response.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
  } catch { return Response.json({ detail: "CRM is temporarily unavailable" }, { status: 503 }); }
}
