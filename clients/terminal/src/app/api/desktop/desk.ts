/** The desktop BFF's shared half (#1783, desktop ADR D1): the person's OWN credential and the one
 *  gateway call. Not a route file — route files export only handlers and route config.
 *
 *  The desk is the person's own state, so it travels with the signed-in person's key (the session
 *  cookie) and never with a deployment key: the catch-all's `resolveApiKey` falls back to
 *  `VEXA_API_KEY`, which would read or arrange a desk nobody signed in for. Same rule as the CRM
 *  record page's proxy (`../crm/route.ts`). */
import { cookies } from "next/headers";
import { AUTH_COOKIE } from "../auth/adminApi";

export const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");

const NO_STORE = { "Content-Type": "application/json", "Cache-Control": "no-store" };

export async function personKey(): Promise<string | null> {
  try { return (await cookies()).get(AUTH_COOKIE)?.value || null; } catch { return null; }
}

/** A browser request from another origin is refused (an absent Origin is a same-origin GET). */
export function otherOrigin(req: Request): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return false;
  const host = req.headers.get("host") || new URL(req.url).host;
  try { return new URL(origin).host !== host; } catch { return true; }
}

export const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: NO_STORE });

/** POST JSON to the gateway with the person's key. Throws when the gateway is unreachable. */
export function gatewayPost(path: string, key: string, body: unknown): Promise<Response> {
  return fetch(`${GATEWAY_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": key },
    body: JSON.stringify(body), cache: "no-store", signal: AbortSignal.timeout(15000),
  });
}

/** An upstream answer, as given: its status and its body (a refusal stays a refusal). */
export async function relay(upstream: Response): Promise<Response> {
  return new Response(await upstream.text(), { status: upstream.status, headers: NO_STORE });
}
