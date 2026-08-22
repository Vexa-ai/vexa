/** LOCAL-DEV SEAM (v-laptop only) — seed a freshly created room's README index.
 *
 *  The product path for workspace writes is an agent turn; on the laptop the agent runner has no
 *  model credential yet (BYOT is the open decision), so this route writes the seed README
 *  directly into the shared workspace's directory inside the agent-api container and commits it.
 *  Guarded to development + minutes mode; returns 404 otherwise so it cannot exist in prod.
 */
import { NextResponse, type NextRequest } from "next/server";
import { execFile } from "child_process";
import { promisify } from "util";
const run = promisify(execFile);

export const dynamic = "force-dynamic";
const CONTAINER = process.env.MINUTES_SEED_CONTAINER || "vexa-v012-agent-api-1";

export async function POST(req: NextRequest) {
  if (process.env.NODE_ENV !== "development" || process.env.NEXT_PUBLIC_TERMINAL_MODE !== "minutes") {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  let body: { wsId?: string; name?: string; purpose?: string; matters?: string };
  try { body = await req.json(); } catch { return NextResponse.json({ error: "bad body" }, { status: 400 }); }
  const wsId = (body.wsId || "").trim();
  if (!/^[A-Za-z0-9_-]+$/.test(wsId)) return NextResponse.json({ error: "bad wsId" }, { status: 400 });
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

  const dir = `/workspaces/${wsId}`;
  // Content travels as base64 in argv — promisified execFile has no stdin, and base64 sidesteps
  // every shell-quoting hazard in user-authored text.
  const b64 = Buffer.from(readme, "utf-8").toString("base64");
  try {
    await run("docker", ["exec", CONTAINER, "sh", "-c",
      `echo '${b64}' | base64 -d > ${dir}/README.md && cd ${dir} && git add README.md && git -c user.email=minutes@local -c user.name="Minutes seed" commit -q -m "room seed: name, purpose, attention list" || true`,
    ], { timeout: 15000, maxBuffer: 1 << 20 });
  } catch (e) {
    return NextResponse.json({ error: String((e as Error).message).slice(0, 300) }, { status: 502 });
  }
  return NextResponse.json({ seeded: true, wsId, address: addr });
}
