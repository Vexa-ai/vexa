import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getAuthenticatedUserId } from "@/lib/auth-utils";
import { getAuthCookieName } from "@/lib/auth-cookies";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
// Service-to-service token — must match BOT_API_TOKEN in the agent-api container
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

async function getUserToken(): Promise<string> {
  const cookieStore = await cookies();
  return cookieStore.get(getAuthCookieName())?.value || "";
}

async function requireUserId(): Promise<string | Response> {
  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return NextResponse.json({ error: "Not authenticated" }, { status: 401 });
  }
  return userId;
}

function pathString(path: string[]): string {
  return path.join("/");
}

function bindQueryUserId(req: NextRequest, userId: string): string {
  const url = new URL(req.url);
  url.searchParams.set("user_id", userId);
  const query = url.searchParams.toString();
  return query ? `?${query}` : "";
}

async function bindJsonBody(req: NextRequest, userId: string): Promise<string | undefined> {
  const rawBody = await req.text();
  if (!rawBody) return undefined;

  const contentType = req.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return rawBody;

  const body = JSON.parse(rawBody);
  body.user_id = userId;
  return JSON.stringify(body);
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
  const userIdOrResponse = await requireUserId();
  if (typeof userIdOrResponse !== "string") return userIdOrResponse;

  const { path } = await context.params;
  const target = `${AGENT_API_URL}/api/${pathString(path)}${bindQueryUserId(req, userIdOrResponse)}`;
  const resp = await fetch(target, {
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
  });
  return safeJsonResponse(resp);
}

export async function POST(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const userIdOrResponse = await requireUserId();
  if (typeof userIdOrResponse !== "string") return userIdOrResponse;

  const { path } = await context.params;
  const pathName = pathString(path);
  const target = `${AGENT_API_URL}/api/${pathName}${bindQueryUserId(req, userIdOrResponse)}`;

  // For chat endpoint: inject user's bot token into request so agent container gets it
  if (pathName === "chat") {
    const rawBody = await req.text();
    const body = rawBody ? JSON.parse(rawBody) : {};
    body.user_id = userIdOrResponse;
    body.bot_token = await getUserToken(); // Agent API will pass this to the container for vexa CLI calls

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
    body: await bindJsonBody(req, userIdOrResponse),
  });
  return safeJsonResponse(resp);
}

export async function PUT(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const userIdOrResponse = await requireUserId();
  if (typeof userIdOrResponse !== "string") return userIdOrResponse;

  const { path } = await context.params;
  const target = `${AGENT_API_URL}/api/${pathString(path)}${bindQueryUserId(req, userIdOrResponse)}`;
  const resp = await fetch(target, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body: await bindJsonBody(req, userIdOrResponse),
  });
  return safeJsonResponse(resp);
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const userIdOrResponse = await requireUserId();
  if (typeof userIdOrResponse !== "string") return userIdOrResponse;

  const { path } = await context.params;
  const target = `${AGENT_API_URL}/api/${pathString(path)}${bindQueryUserId(req, userIdOrResponse)}`;
  const body = await bindJsonBody(req, userIdOrResponse);
  const resp = await fetch(target, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body,
  });
  return safeJsonResponse(resp);
}
