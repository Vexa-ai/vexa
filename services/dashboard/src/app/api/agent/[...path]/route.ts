import { NextRequest } from "next/server";
import { cookies } from "next/headers";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
// Service-to-service token — must match BOT_API_TOKEN in the agent-api container
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

async function getUserToken(): Promise<string> {
  const cookieStore = await cookies();
  return cookieStore.get("vexa-token")?.value || "";
}

async function safeJsonResponse(resp: globalThis.Response): Promise<Response> {
  const text = await resp.text();
  try {
    return Response.json(JSON.parse(text), { status: resp.status });
  } catch {
    return new Response(text, {
      status: resp.status,
      headers: { "Content-Type": resp.headers.get("content-type") || "text/plain" },
    });
  }
}

export async function GET(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(req.url);
  const target = `${AGENT_API_URL}/api/${path.join("/")}${url.search}`;
  const resp = await fetch(target, {
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
  });
  return safeJsonResponse(resp);
}

export async function POST(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(req.url);
  const rawBody = await req.text();
  const target = `${AGENT_API_URL}/api/${path.join("/")}${url.search}`;

  // For chat endpoint: inject user's bot token into request so agent container gets it
  if (path.join("/") === "chat") {
    const userToken = await getUserToken();
    const body = JSON.parse(rawBody);
    body.bot_token = userToken; // Agent API will pass this to the container for vexa CLI calls

    const resp = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
      body: JSON.stringify(body),
    });

    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  }

  const resp = await fetch(target, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body: rawBody,
  });
  return safeJsonResponse(resp);
}

export async function PUT(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(req.url);
  const body = await req.text();
  const target = `${AGENT_API_URL}/api/${path.join("/")}${url.search}`;
  const resp = await fetch(target, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body,
  });
  return safeJsonResponse(resp);
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const url = new URL(req.url);
  const body = await req.text();
  const target = `${AGENT_API_URL}/api/${path.join("/")}${url.search}`;
  const resp = await fetch(target, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body: body || undefined,
  });
  return safeJsonResponse(resp);
}
