/** PROJECTS — a user's private bundles of workspaces to chat over. Not shared, not a backend
 *  object (yet): a client-side registry. Two built-ins always exist: Personal (over the personal
 *  workspace) and Organisation (admin, over _global). */
export type Project = { id: string; name: string; set: string[]; builtin?: "personal" | "org"; chats: { id: string; label: string }[] };

const KEY = "vexa.minutes.projects";

export function loadProjects(): Project[] {
  let stored: Project[] = [];
  try { stored = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { /* fresh */ }
  const out: Project[] = [];
  const personal = stored.find((p) => p.builtin === "personal")
    ?? { id: "personal", name: "Personal", set: ["personal"], builtin: "personal" as const, chats: [] };
  const org = stored.find((p) => p.builtin === "org")
    ?? { id: "org", name: "Organisation", set: ["_global"], builtin: "org" as const, chats: [] };
  out.push(personal, ...stored.filter((p) => !p.builtin), org);
  return out;
}

export function saveProjects(ps: Project[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(ps)); } catch { /* ignore */ }
}
