"use client";
/** "TEST USERS…" — app.dev tooling (#1783): sign in as a test account without OAuth or email. Lists
 *  the `@example.test` accounts, makes a new one, and hands a one-time sign-in link (15 minutes,
 *  single use) to copy or to open in a new window. Shown only to an administrator on a deployment
 *  that switched it on; the server refuses everyone else. */
import { useEffect, useState } from "react";
import { Button, Dialog, Input } from "../ui-kit";
import "./minutes.css";

type TestUser = { id: string; email: string; name?: string | null };

async function json<T>(r: Response): Promise<T> {
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error((body as { error?: string }).error || `Request failed (${r.status})`);
  return body as T;
}

export function TestUsersDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [users, setUsers] = useState<TestUser[] | null>(null);
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    try { setUsers((await json<{ users: TestUser[] }>(await fetch("/api/auth/test-users", { cache: "no-store" }))).users); }
    catch (e) { setNote((e as Error).message); setUsers([]); }
  };
  useEffect(() => { if (open) { setNote(""); void load(); } }, [open]);
  const link = async (email: string): Promise<string | null> => {
    try {
      return (await json<{ url: string }>(await fetch("/api/auth/test-users/link", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email }),
      }))).url;
    } catch (e) { setNote((e as Error).message); return null; }
  };
  return (
    <Dialog open={open} onClose={onClose} title="Test users" width="form"
      footer={<Button variant="ghost" onClick={onClose}>Close</Button>}>
      <div className="vx-testusers">
        <form className="vx-testusers-new" onSubmit={async (e) => {
          e.preventDefault(); if (!name.trim()) return; setBusy(true); setNote("");
          try {
            const r = await json<{ user: TestUser }>(await fetch("/api/auth/test-users", {
              method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }),
            }));
            setName(""); setNote(`Created ${r.user.email}`); await load();
          } catch (x) { setNote((x as Error).message); } finally { setBusy(false); }
        }}>
          <Input label="New test user" placeholder="Name, e.g. Ann" value={name} onChange={(e) => setName(e.target.value)} />
          <Button type="submit" disabled={busy || !name.trim()}>Create</Button>
        </form>
        {note && <p role="status" className="vx-testusers-note">{note}</p>}
        <p className="vx-testusers-hint">A sign-in link works once, for 15 minutes. Opening it signs that window in as the test user — use a private window to stay signed in as yourself.</p>
        <ul className="vx-testusers-list" aria-label="Test users">
          {users === null && <li>Loading…</li>}
          {users?.length === 0 && <li>No test users yet.</li>}
          {users?.map((u) => (
            <li key={u.id} data-test-user={u.email}>
              <span className="vx-testusers-email">{u.email}</span>
              <Button size="sm" variant="ghost" onClick={async () => {
                const url = await link(u.email);
                if (url) { try { await navigator.clipboard.writeText(url); setNote(`Sign-in link for ${u.email} copied`); } catch { setNote(url); } }
              }}>Copy sign-in link</Button>
              <Button size="sm" variant="ghost" onClick={async () => {
                const url = await link(u.email);
                if (url) window.open(url, "_blank", "noopener");
              }}>Open in new window</Button>
            </li>
          ))}
        </ul>
      </div>
    </Dialog>
  );
}
