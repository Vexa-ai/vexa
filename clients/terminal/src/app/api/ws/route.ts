/** WS connect helper — hands the browser the gateway `/ws` URL with the api_key embedded.
 *
 *  A browser `WebSocket` can't set an `X-API-Key` header on the upgrade handshake, and a Next.js
 *  route handler can't proxy a raw WS upgrade. So — mirroring the SSE proxy (`meeting/stream/route.ts`)
 *  which injects the key server-side — this route keeps `VEXA_BOT_API_KEY` server-side and returns the
 *  gateway WS URL with `?api_key=…` appended. The gateway resolves the user_id from that key at connect
 *  (Track ①) and auto-subscribes the socket to `u:{user_id}:meetings`; the client just opens the URL.
 *
 *  Same-origin preference: if the deploy sets `VEXA_API_URL`, next.config rewrites same-origin `/ws`
 *  to it, so we return a same-origin `wss?://<host>/ws?api_key=…`. Otherwise we point straight at
 *  GATEWAY_URL (http→ws). Either way the key never reaches client bundle code.
 */
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const GATEWAY_URL = (process.env.GATEWAY_URL || "http://127.0.0.1:18056").replace(/\/$/, "");
const BOT_KEY = process.env.VEXA_BOT_API_KEY || "";

export async function GET(req: NextRequest) {
  const q = BOT_KEY ? `?api_key=${encodeURIComponent(BOT_KEY)}` : "";
  let url: string;
  if (process.env.VEXA_API_URL) {
    // same-origin /ws is rewritten to VEXA_API_URL by next.config — derive ws(s) from the request origin
    const proto = req.nextUrl.protocol === "https:" ? "wss:" : "ws:";
    url = `${proto}//${req.nextUrl.host}/ws${q}`;
  } else {
    url = `${GATEWAY_URL.replace(/^http/, "ws")}/ws${q}`;
  }
  return NextResponse.json({ url });
}
