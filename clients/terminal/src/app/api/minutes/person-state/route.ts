/** MINUTES person-state — the SERVER-side guard for once-ever flows (dev seam).
 *
 *  GET  ?key=<flag>          → { set: boolean }
 *  POST { key }              → sets the flag
 *
 *  Flags live in the user's PRIVATE system workspace (`.system/<uid>/minutes-state.json`), so
 *  "has this person been onboarded / door-kicked?" survives browsers, devices and reloads —
 *  the localStorage guard it replaces only remembered a browser profile, which is why a fresh
 *  profile re-greeted a known person. 404 outside local-dev minutes mode.
 */
import { execFile } from "child_process";
import { promisify } from "util";
import { cookies } from "next/headers";
import { NextRequest, NextResponse } from "next/server";
import { USER_INFO_COOKIE } from "../../auth/adminApi";

async function userId(): Promise<string | null> {
  try {
    const info = (await cookies()).get(USER_INFO_COOKIE)?.value;
    const id = info ? JSON.parse(info)?.id : null;
    return id != null && /^[0-9]+$/.test(String(id)) ? String(id) : null;
  } catch { return null; }
}

const run = promisify(execFile);
const CONTAINER = process.env.MINUTES_SEED_CONTAINER || "vexa-v012-agent-api-1";

function gated(): NextResponse | null {
  if (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_TERMINAL_MODE !== "minutes")
    return NextResponse.json({ error: "not found" }, { status: 404 });
  return null;
}

async function read(uid: string): Promise<Record<string, boolean>> {
  try {
    const { stdout } = await run("docker", ["exec", CONTAINER, "cat", `/workspaces/.system/${uid}/minutes-state.json`], { timeout: 8000 });
    return JSON.parse(stdout);
  } catch { return {}; }
}

export async function GET(req: NextRequest) {
  const g = gated(); if (g) return g;
  const uid = await userId(); if (!uid) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const key = req.nextUrl.searchParams.get("key") || "";
  if (!/^[a-z0-9._:-]+$/i.test(key)) return NextResponse.json({ error: "bad key" }, { status: 400 });
  const st = await read(uid);
  return NextResponse.json({ set: !!st[key] });
}

export async function POST(req: NextRequest) {
  const g = gated(); if (g) return g;
  const uid = await userId(); if (!uid) return NextResponse.json({ error: "not signed in" }, { status: 401 });
  const { key } = await req.json().catch(() => ({}));
  if (!key || !/^[a-z0-9._:-]+$/i.test(key)) return NextResponse.json({ error: "bad key" }, { status: 400 });
  const st = await read(uid); st[key] = true;
  const b64 = Buffer.from(JSON.stringify(st), "utf-8").toString("base64");
  await run("docker", ["exec", CONTAINER, "sh", "-c",
    `mkdir -p /workspaces/.system/${uid} && echo '${b64}' | base64 -d > /workspaces/.system/${uid}/minutes-state.json`,
  ], { timeout: 8000 });
  return NextResponse.json({ ok: true });
}
