/** PROJECTS — a user's private bundles of workspaces to chat over. Not shared, not a backend
 *  object (yet): a client-side registry. ONE built-in always exists: Personal. Organisation is
 *  merely SEEDED on first run as an ordinary project over `_global` — the admin iterates on the
 *  org tier like any other workspace (ownership: who may WRITE _global is the platform's
 *  allowlist, never a UI special case). Delete it, re-make it, add chats — all fair game. */
export type Project = { id: string; name: string; set: string[]; builtin?: "personal" | "org"; chats: { id: string; label: string }[] };

const KEY = "vexa.minutes.projects";
const SEEDED_KEY = "vexa.minutes.orgSeeded";

/** `_global` is the immutable first tier; `_system` is appended by dispatch, never stored in a project. */
export function projectWorkspaces(set: string[]): string[] {
  return ["_global", ...set.filter((slug, index) => slug !== "_global" && slug !== "_system" && set.indexOf(slug) === index)];
}

export function projectMountStack(set: string[]): string[] {
  return [...projectWorkspaces(set), "_system"];
}

function normalizeProject(project: Project): Project {
  return { ...project, set: projectWorkspaces(project.set) };
}

export function loadProjects(): Project[] {
  let stored: Project[] = [];
  try { stored = JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { /* fresh */ }
  const out: Project[] = [];
  const personal = normalizeProject(stored.find((p) => p.builtin === "personal")
    ?? { id: "personal", name: "Personal", set: ["personal"], builtin: "personal" as const, chats: [] });
  // migrate a legacy builtin "org" row to an ordinary project (keeps its chats)
  const legacyOrg = stored.find((p) => p.builtin === "org");
  const rest = stored.filter((p) => !p.builtin).map(normalizeProject);
  if (legacyOrg) rest.unshift(normalizeProject({ id: "org", name: "Organisation", set: ["_global"], chats: legacyOrg.chats.length ? legacyOrg.chats : [{ id: "org-setup", label: "setup" }] }));
  let seeded = false;
  try { seeded = !!localStorage.getItem(SEEDED_KEY); } catch { /* ignore */ }
  if (!legacyOrg && !seeded && !rest.some((p) => p.id === "org")) {
    rest.unshift(normalizeProject({ id: "org", name: "Organisation", set: ["_global"], chats: [{ id: "org-setup", label: "setup" }] }));
  }
  try { localStorage.setItem(SEEDED_KEY, "1"); } catch { /* ignore */ }
  out.push(personal, ...rest);
  saveProjects(out);
  return out;
}

export function saveProjects(ps: Project[]): void {
  try { localStorage.setItem(KEY, JSON.stringify(ps.map(normalizeProject))); } catch { /* ignore */ }
}
