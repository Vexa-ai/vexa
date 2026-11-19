/** `GET` the reserved-domain test users; `POST {name}` makes `tester-<slug>@example.test` through
 *  admin-api's ordinary create path. Gated by `testUsers.ts` (switched on, instance admin). */
import { NextResponse, type NextRequest } from "next/server";
import { createUser, listTestUsers } from "../adminApi";
import { NO_STORE, adminGate, testAddressFor } from "../testUsers";

export const dynamic = "force-dynamic";

export async function GET() {
  const gate = await adminGate();
  if (!gate.ok) return gate.res;
  const r = await listTestUsers();
  if (!r.ok) return NextResponse.json({ error: "Could not list test users" }, { status: 502, headers: NO_STORE });
  return NextResponse.json({ users: (r.data ?? []).map((u) => ({ id: String(u.id), email: u.email, name: u.name ?? null })) }, { headers: NO_STORE });
}

export async function POST(request: NextRequest) {
  const gate = await adminGate();
  if (!gate.ok) return gate.res;
  let name = "";
  try { name = String(((await request.json()) as { name?: unknown }).name ?? ""); } catch { /* empty */ }
  const email = testAddressFor(name);
  if (!email) return NextResponse.json({ error: "Give the test user a name" }, { status: 400, headers: NO_STORE });
  const r = await createUser(email);
  if (!r.ok || !r.data) return NextResponse.json({ error: "Could not create the test user" }, { status: 502, headers: NO_STORE });
  console.info(`[terminal-auth] test user ${email} created by ${gate.email}`);
  return NextResponse.json({ user: { id: String(r.data.id), email: r.data.email, name: r.data.name ?? null } }, { headers: NO_STORE });
}
