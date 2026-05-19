import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { getAuthCookieName } from "@/lib/auth-cookies";
import { getAuthenticatedUserId } from "@/lib/auth-utils";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
// Service-to-service token — must match BOT_API_TOKEN in the agent-api container
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

async function getUserToken(): Promise<string> {
  const cookieStore = await cookies();
  return cookieStore.get(getAuthCookieName())?.value || "";
}

async function requireAgentUser(): Promise<{ userId: string; userToken: string } | Response> {
  const userToken = await getUserToken();
  if (!userToken) {
    return Response.json({ error: "Not authenticated" }, { status: 401 });
  }

  const userId = await getAuthenticatedUserId();
  if (!userId) {
    return Response.json({ error: "Invalid session" }, { status: 401 });
  }

  return { userId, userToken };
}

function isAllowedPath(path: string[]): boolean {
  const root = path[0] || "";
  return ["chat", "sessions", "workspaces", "workspace", "schedule"].includes(root);
}

function buildTarget(path: string[], search: string, userId: string): string {
  const params = new URLSearchParams(search);
  if (params.has("user_id")) {
    params.set("user_id", userId);
  }
  const query = params.toString();
  return `${AGENT_API_URL}/api/${path.join("/")}${query ? `?${query}` : ""}`;
}

function withCanonicalUser(rawBody: string, userId: string, botToken?: string): string {
  if (!rawBody) {
    return JSON.stringify(botToken ? { user_id: userId, bot_token: botToken } : { user_id: userId });
  }

  const body = JSON.parse(rawBody);
  if (body && typeof body === "object" && !Array.isArray(body)) {
    body.user_id = userId;
    if (botToken !== undefined) {
      body.bot_token = botToken;
    }
    return JSON.stringify(body);
  }

  return rawBody;
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
  if (!isAllowedPath(path)) {
    return Response.json({ error: "Agent endpoint not allowed" }, { status: 404 });
  }
  const auth = await requireAgentUser();
  if (auth instanceof Response) return auth;
  const url = new URL(req.url);
  const target = buildTarget(path, url.search, auth.userId);
  const resp = await fetch(target, {
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
  });
  return safeJsonResponse(resp);
}

export async function POST(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!isAllowedPath(path)) {
    return Response.json({ error: "Agent endpoint not allowed" }, { status: 404 });
  }
  const auth = await requireAgentUser();
  if (auth instanceof Response) return auth;
  const url = new URL(req.url);
  const rawBody = await req.text();
  const target = buildTarget(path, url.search, auth.userId);

  // For chat endpoint: inject user's bot token into request so agent container gets it
  if (path.join("/") === "chat") {
    const body = withCanonicalUser(rawBody, auth.userId, auth.userToken);

    const resp = await fetch(target, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
      body,
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
    body: withCanonicalUser(rawBody, auth.userId),
  });
  return safeJsonResponse(resp);
}

export async function PUT(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!isAllowedPath(path)) {
    return Response.json({ error: "Agent endpoint not allowed" }, { status: 404 });
  }
  const auth = await requireAgentUser();
  if (auth instanceof Response) return auth;
  const url = new URL(req.url);
  const body = await req.text();
  const target = buildTarget(path, url.search, auth.userId);
  const resp = await fetch(target, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body: withCanonicalUser(body, auth.userId),
  });
  return safeJsonResponse(resp);
}

export async function DELETE(req: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  if (!isAllowedPath(path)) {
    return Response.json({ error: "Agent endpoint not allowed" }, { status: 404 });
  }
  const auth = await requireAgentUser();
  if (auth instanceof Response) return auth;
  const url = new URL(req.url);
  const body = await req.text();
  const target = buildTarget(path, url.search, auth.userId);
  const resp = await fetch(target, {
    method: "DELETE",
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
    body: body ? withCanonicalUser(body, auth.userId) : undefined,
  });
  return safeJsonResponse(resp);
}
