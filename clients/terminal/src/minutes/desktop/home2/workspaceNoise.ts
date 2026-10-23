/** WHAT A PERSON SEES OF A WORKSPACE (JTBD-1 noise, home2). The workspace page lists the person's
 *  own pages and human events; the agent's scaffolding (instructions, flows, skills, policies) and
 *  the commit plumbing behind every write stay out of sight. */
import type { GitCommit } from "../../../data/workspaceApi";

const SCAFFOLD_FILES = /^(claude|agents)\.md$/i;
const SCAFFOLD_DIRS = /^(flows|skills|policies|templates|\.[^/]*|_[^/]*)\//i;

export function isScaffolding(path: string): boolean {
  return SCAFFOLD_FILES.test(path) || SCAFFOLD_DIRS.test(path);
}

/** The pages a person reads: README first, then their own, never scaffolding or dotfiles. */
export function peoplePages(tree: string[]): string[] {
  const pages = tree.filter((f) => /\.md$/i.test(f) && !f.startsWith(".") && !isScaffolding(f));
  const readme = pages.find((f) => /^readme\.md$/i.test(f));
  return [...(readme ? [readme] : []), ...pages.filter((f) => f !== readme)];
}

const PLUMBING = /^(policy|policies|seed|workspace identity|identity|scaffold|init|sync|merge|chore|wip)\b/i;
const EDIT = /^edit (\S+)(?: \(.*\))?$/i;
const pageWord = (p: string) => (p.split("/").pop() || p).replace(/\.md$/i, "");

/** Only events a person would say out loud: no system commits, no plumbing lines; an editor's
 *  "edit X.md (…)" reads "Edited X". */
export function humanActivity<C extends Pick<GitCommit, "msg"> & { kind?: string }>(commits: C[]): C[] {
  const out: C[] = [];
  for (const c of commits) {
    if (c.kind === "system") continue;
    const msg = String(c.msg ?? "").trim();
    if (!msg || PLUMBING.test(msg)) continue;
    const e = EDIT.exec(msg);
    out.push(e ? { ...c, msg: `Edited ${pageWord(e[1])}` } : c);
  }
  return out;
}

/** The home's chips: "+ New workspace" is the one way to make a workspace there (home2). */
export function homeChips<T extends { kind?: string }>(chips: T[], home2: boolean): T[] {
  return home2 ? chips.filter((c) => c.kind !== "shared-workspace") : chips;
}
