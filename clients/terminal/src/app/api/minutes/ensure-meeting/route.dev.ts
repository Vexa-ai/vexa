/** MINUTES dev seam: make sure the signed-in user HAS the meeting a door link names.
 *
 *  POST { platform, native, title? } → finds-or-creates the user's meeting row via the gateway
 *  (mints a 15-minute system key through admin-api, exactly what the mailroom does in prod). The door
 *  must never land on a void: whoever the invitation reached owns a view of that meeting.
 *  404 outside local-dev minutes mode, and not built at all into a production build (`route.dev.ts`,
 *  next.config.ts `pageExtensions`).
 */
import { NextRequest, NextResponse } from "next/server";
import { mintUserToken } from "../../auth/adminApi";
import { currentUser } from "../../tokens/currentUser";

const GATEWAY = process.env.VEXA_GATEWAY_PUBLIC_URL || "http://127.0.0.1:18056";
const URLS: Record<string, (n: string) => string> = {
  google_meet: (n) => `https://meet.google.com/${n}`,
};

export async function POST(req: NextRequest) {
  if (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_TERMINAL_MODE !== "minutes")
    return NextResponse.json({ error: "not found" }, { status: 404 });
  // WHO, from the validated session cookie: a token is minted for this person below, so the
  // display-only info cookie (anybody can write one) never names them.
  const me = await currentUser();
  if (!me.ok) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const uid = String(me.userId);

  const { platform, native, title } = await req.json().catch(() => ({}));
  const mk = URLS[String(platform)];
  if (!mk || !/^[a-z0-9-]+$/i.test(String(native || "")))
    return NextResponse.json({ error: "bad ref" }, { status: 400 });

  const tok = await mintUserToken(uid, {
    scopes: ["bot", "tx"], name: "minutes-dev", expiresIn: 15 * 60, credentialClass: "system",
  });
  if (!tok.ok) return NextResponse.json({ error: "token mint failed" }, { status: 502 });
  const key = (tok.data as { token: string }).token;

  const list = await fetch(`${GATEWAY}/meetings`, { headers: { "X-API-Key": key }, cache: "no-store" }).then((r) => r.json()).catch(() => null);
  const have = list?.meetings?.find((m: { native_meeting_id?: string }) => m.native_meeting_id === native);
  if (have) return NextResponse.json({ ok: true, id: have.id, created: false });

  const r = await fetch(`${GATEWAY}/meetings`, {
    method: "POST", headers: { "X-API-Key": key, "content-type": "application/json" },
    body: JSON.stringify({ meeting_url: mk(native), auto_join: false, title: title || undefined }),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) return NextResponse.json({ error: body?.detail || "create failed" }, { status: r.status });
  return NextResponse.json({ ok: true, id: body.id, created: true });
}
