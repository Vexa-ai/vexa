/** Which panel tabs a chat may keep, given its scope (Vexa-ai/vexa#1783). A tab of the person's own
 *  desk (no `slug`) stays only while the chat reads or writes Personal; a tab of a workspace the person
 *  no longer has (hidden, deleted, never theirs) goes. Meetings and the system tiers are not workspace
 *  tabs and always stay. Pure: the same array back when nothing is dropped. */
export function dropStaleTabs<T extends { kind?: string; slug?: string }>(
  pages: T[], scope: { reads: string[]; target: string | null; known: string[] },
): T[] {
  const desk = scope.reads.includes("personal") || scope.target === "personal";
  const known = new Set(scope.known);
  const keep = (p: T) => {
    if (p.kind === "meeting") return true;
    if (!p.slug) return desk;
    if (p.slug.startsWith("_")) return true;
    return known.has(p.slug);
  };
  const out = pages.filter(keep);
  return out.length === pages.length ? pages : out;
}

/** Does an artifact the turn produced open the panel by itself? Only a MEETING the chat just started
 *  (its live view is pinned); a file the agent wrote stays a chip in the chat until it is clicked
 *  (Vexa-ai/vexa#1783, UI decision 7 amended). */
export function autoOpens(detail: { path?: string; workspace?: string; focus?: boolean }): boolean {
  return String(detail.path ?? "").startsWith("meeting:");
}

/** The tabs a chat opens with: the ones the person opened in THIS chat (and a meeting chat's own
 *  meeting pages) — never an auto-opened desk or workspace README. */
export function openingStrip<T extends { kind?: string; desk?: boolean; home?: boolean; permanent?: boolean }>(
  stored: T[], chat: { meeting: boolean },
): T[] {
  void chat;   // a meeting chat's meeting pages are kept like any tab the chat has
  return stored.filter((t) => !t.desk && !t.home);
}

/** The panel is drawn only when it has something: a tab, or the Files listing the person opened. */
export function panelShows(tabs: number, files: boolean): boolean {
  return tabs > 0 || files;
}
