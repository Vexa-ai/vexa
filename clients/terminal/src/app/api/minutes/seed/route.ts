/** LOCAL-DEV SEAM (v-laptop only) — seed a freshly created room's README index.
 *
 *  The product path for workspace writes is an agent turn; on the laptop the agent runner has no
 *  model credential yet (BYOT is the open decision), so this route writes the seed README
 *  directly into the shared workspace's directory inside the agent-api container and commits it.
 *  Guarded to development + minutes mode; returns 404 otherwise so it cannot exist in prod.
 *
 *  SHELL INJECTION, FIXED (R-E12). `f.b64` was interpolated into a `sh -c` string inside single
 *  quotes and validated nowhere, so one apostrophe in the payload closed the quote and the rest
 *  ran as root in the agent-api container. It is a developer's own laptop, which bounds the blast
 *  radius and does not make it safe: the body arrives over HTTP and this route has no identity
 *  check at all. Two changes: base64 is validated against its own alphabet and travels on STDIN,
 *  never through argv; and every path is passed as a positional parameter to the shell rather
 *  than spliced into its text.
 */
import { NextResponse, type NextRequest } from "next/server";
import { dockerSh } from "./dockerSh";

export const dynamic = "force-dynamic";
const CONTAINER = process.env.MINUTES_SEED_CONTAINER || "vexa-v012-agent-api-1";

/** Strict base64 — the only characters the payload may contain, so nothing can leave stdin. */
const B64 = /^[A-Za-z0-9+/=]+$/;

export async function POST(req: NextRequest) {
  if (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_TERMINAL_MODE !== "minutes") {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  let body: { wsId?: string; name?: string; purpose?: string; matters?: string;
              files?: { path: string; b64: string }[] };
  try { body = await req.json(); } catch { return NextResponse.json({ error: "bad body" }, { status: 400 }); }
  const wsId = (body.wsId || "").trim();
  if (!/^[A-Za-z0-9_-]+$/.test(wsId)) return NextResponse.json({ error: "bad wsId" }, { status: 400 });
  // files mode: write given files (base64) into the workspace and commit — used to pre-seed a
  // cold-start workspace with the meeting artifact page before the reader's first visit.
  if (Array.isArray(body.files) && body.files.length) {
    for (const f of body.files) {
      if (!/^[A-Za-z0-9._/-]+$/.test(f.path) || f.path.includes("..")) return NextResponse.json({ error: "bad path" }, { status: 400 });
      if (typeof f.b64 !== "string" || !B64.test(f.b64)) return NextResponse.json({ error: "bad b64" }, { status: 400 });
      try {
        await dockerSh(CONTAINER,
          'mkdir -p "$(dirname "$1/$2")" && base64 -d > "$1/$2" && cd "$1" && git add "$2" && ' +
          'git -c user.email=minutes@local -c user.name="Minutes seed" commit -q -m "minutes seed: $2" || true',
          [`/workspaces/${wsId}`, f.path], f.b64);
      } catch (e) { return NextResponse.json({ error: String((e as Error).message).slice(0, 300) }, { status: 502 }); }
    }
    return NextResponse.json({ ok: true, wrote: body.files.map((f) => f.path) });
  }
  const name = (body.name || wsId).trim();
  const purpose = (body.purpose || "").trim();
  const matters = (body.matters || "").trim();
  const addr = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") + "@meetings.local";

  const readme = [
    `# ${name}`,
    "",
    purpose ? `> ${purpose}` : "",
    "",
    `**Address:** \`${addr}\` — invite it to this room's meeting like a colleague.`,
    "",
    "## What this room pays attention to",
    "",
    matters ? matters.split(/\n+/).map((l) => `- ${l.trim()}`).filter((l) => l !== "- ").join("\n") : "_(set during onboarding)_",
    "",
    "## Where things stand",
    "",
    "_Nothing yet — this section fills in after the first meeting._",
    "",
    "## Meetings",
    "",
    "_Each meeting appears here as a dated link once it has happened._",
    "",
  ].join("\n");

  // Content travels as base64 on STDIN. It is ours, not the caller's, so it cannot be hostile —
  // but the route that WAS hostile used the identical splice, so there is one way to do this here.
  const b64 = Buffer.from(readme, "utf-8").toString("base64");
  try {
    await dockerSh(CONTAINER,
      'base64 -d > "$1/README.md" && cd "$1" && git add README.md && ' +
      'git -c user.email=minutes@local -c user.name="Minutes seed" commit -q -m "room seed: name, purpose, attention list" || true',
      [`/workspaces/${wsId}`], b64);
  } catch (e) {
    return NextResponse.json({ error: String((e as Error).message).slice(0, 300) }, { status: 502 });
  }
  return NextResponse.json({ seeded: true, wsId, address: addr });
}
