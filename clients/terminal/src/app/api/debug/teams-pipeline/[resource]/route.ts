import { readFile } from "node:fs/promises";

export const dynamic = "force-dynamic";

function enabled(): boolean {
  return process.env.NODE_ENV === "development"
    && !!process.env.VEXA_TEAMS_PIPELINE_BACKEND
    && !!process.env.VEXA_TEAMS_PIPELINE_REFERENCE;
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ resource: string }> },
): Promise<Response> {
  if (!enabled()) return new Response("not found\n", { status: 404 });
  const { resource } = await params;
  if (resource === "reference") {
    const bytes = await readFile(process.env.VEXA_TEAMS_PIPELINE_REFERENCE!);
    return new Response(bytes, {
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" },
    });
  }
  if (!["events", "start", "status", "audio"].includes(resource)) return new Response("not found\n", { status: 404 });
  const base = process.env.VEXA_TEAMS_PIPELINE_BACKEND!.replace(/\/+$/, "");
  const headers = new Headers();
  const range = request.headers.get("range");
  if (range) headers.set("range", range);
  const upstream = await fetch(`${base}/${resource === "audio" ? "audio.wav" : resource}`, {
    cache: "no-store",
    headers,
  });
  const responseHeaders = new Headers({
    "content-type": upstream.headers.get("content-type") ?? "application/octet-stream",
    "cache-control": "no-store",
  });
  for (const name of ["accept-ranges", "content-length", "content-range"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}
