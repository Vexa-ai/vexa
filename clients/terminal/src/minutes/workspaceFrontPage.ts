/** THE FRONT PAGE AS TWO SENTENCES — where you are and who is here, then what last happened and
 *  what you may do about it (Vexa-ai/vexa#1634).
 *
 *  Founder, 2026-09-06, on the strip #1628 had just sized down to the header — *"Company layer · 25
 *  pages · _global: asks/policies-wizard.md … · dmitry@vexa.ai · 14 minutes ago · Everyone reads,
 *  the admin writes · no repo attached · 10+ commits"*: **"what about this one? never spoke about
 *  how to make it right, helpful and nice."** #1628 fixed the size. The content was still a list of
 *  repository facts — a path, an address, a commit count, a repo state — six answers to six
 *  questions nobody had asked. **The person needs a sentence about a place.**
 *
 *  So the strip is two lines, and every function in this file exists to make one clause of them:
 *
 *      Company layer · everyone at Acme reads it, Jane writes it · 25 pages
 *      Changed 14 minutes ago by Jane Smith: the policies wizard ask · policies: default profile
 *          [Set up policies] [Add an editor] [History]
 *
 *  FOUR RULES ARE IN HERE, AND THEY ARE ALL SUBTRACTIONS.
 *
 *  1. **People as names, "you" first** — and where people are not the point (a desk, the company
 *     layer) a VISIBILITY sentence instead, derived from `POLICIES.md`'s own answers rather than
 *     retyped. Roles are behind the disclosure: *reader* beside a name is a fact about permissions,
 *     and the first line is about who is here.
 *  2. **The changed thing by its TITLE, the author by their NAME.** Both are resolved on the server
 *     (`control_plane/front_page.py`), because neither is in a git log: `%s` is a commit subject and
 *     `%an` is the principal a mount commits as. This file only chooses the WORDS around them.
 *  3. **An address is never a name.** The server answers `null` when nobody has written a person
 *     down, and `null` renders as *someone*. `dmitry@vexa.ai` in that position — which is what the
 *     founder was shown — tells the reader the product does not know who works here.
 *  4. **Nothing that is not a sentence.** No commit count, no *no repo attached*, no address, no
 *     path (#1634 rule 3). Those are still true and still one click away, in the sections #1628
 *     built, behind the one disclosure at the end of line two.
 *
 *  WHY THIS FILE IS PURE. Every line above is a claim about words, and words are the thing a test
 *  can hold exactly. `workspaceReadme.ts` does the reading, `WorkspaceReadmePanel.tsx` does the
 *  rendering, and the three sentences the founder asked for are decided here from fixture data.
 */
import type { GitRemoteStatus, LastChange } from "../surfaces/workspaceApi";
import type { BoundSeries, Role, WorkspaceKind } from "./workspaceReadme";

export type { ChangedPage, LastChange } from "../surfaces/workspaceApi";

// ── what the server hands us ────────────────────────────────────────────────────────────────────

/** A member as the first line needs them: a subject, and a name if anybody has written one. */
export interface NamedMember { subject: string; role?: string; name?: string | null; email?: string }

// ── front matter, the two answers this file reads out of `POLICIES.md` ──────────────────────────

/** One scalar out of a leading `---` block. Deliberately not a YAML parser: the two keys read here
 *  (`profile`, and the on/off of one rule) are scalars on their own line in the file the product
 *  ships and in every file the wizard writes. */
export function frontMatterValue(md: string | null, key: string): string | null {
  const text = String(md ?? "");
  if (!text.startsWith("---")) return null;
  const end = text.indexOf("\n---", 3);
  if (end === -1) return null;
  const m = new RegExp(`^${key}\\s*:\\s*(.*)$`, "mi").exec(text.slice(3, end));
  const v = m ? m[1].trim().replace(/^['"]|['"]$/g, "") : "";
  return v || null;
}

/** THE POLICY PROFILE — `profile:` in `_global/POLICIES.md` (#1634 rule 4: the company layer's own
 *  kind-specific fact). It is the one thing about the company layer that is a decision rather than
 *  a count, and `POLICIES.md` § Profiles is where it is written down. */
export const policyProfile = (policies: string | null): string | null =>
  frontMatterValue(policies, "profile");

/** THE COMPANY'S NAME — the first heading of `_global/README.md`, which is where the setup
 *  conversation puts it and what `global_layer.company_of` reads on the server before it lifts the
 *  gate. Read, never retyped: an instance that renames itself renames this line with it.
 *
 *  The placeholder words are refused for the same reason that function refuses them — this string
 *  goes into a sentence about somebody's employer, and *everyone at Setup reads it* is worse than
 *  no name at all. */
const PLACEHOLDER = new Set(["company", "your company", "unknown", "tbd", "readme", "_global", "global"]);
export function companyName(readme: string | null): string | null {
  for (const line of String(readme ?? "").split("\n")) {
    if (!line.trim()) continue;
    const m = /^#\s+(.+?)\s*$/.exec(line);
    if (!m) return null;                       // the first thing is not a heading — no name is stated
    const name = m[1].trim();
    return !name || PLACEHOLDER.has(name.toLowerCase()) ? null : name;
  }
  return null;
}

// ── line one: where you are, and who is here ────────────────────────────────────────────────────

/** WHO MAY READ THIS PLACE, in one clause — for the two kinds where people are not the point.
 *
 *  DERIVED FROM `POLICIES.md`'s ANSWERS, not from its prose. #1623 lifted the rule's own heading
 *  sentence ("an agent may read its user's desk when its user is a participant"), which is right for
 *  a Policy row in a facts table and wrong for the first line a person reads: it is a rule, and this
 *  is a place. So the on/off of the one rule that governs each kind is read out of the front matter
 *  and turned into the sentence the founder wrote.
 *
 *  `null` when the file could not be read — the line then simply has one clause fewer, which is the
 *  panel's standing rule about a fact it does not have. */
export function visibilitySentence(
  kind: WorkspaceKind, policies: string | null,
  who: { company?: string | null; adminFirstName?: string | null } = {},
): string | null {
  if (!policies) return null;
  const on = (key: string) => {
    const v = frontMatterValue(policies, key);
    return v === null ? null : /^(on|true|yes)$/i.test(v);
  };
  if (kind === "desk") {
    const reads = on("agent_reads_desk");
    if (reads === null) return null;
    return reads ? "agents read it for meetings you are in" : "no agent reads it";
  }
  if (kind === "global") {
    const adminOnly = on("global_admin_only");
    if (adminOnly === null) return null;
    const everyone = who.company ? `everyone at ${who.company}` : "everyone here";
    const writer = who.adminFirstName || "the admin";
    return adminOnly ? `${everyone} reads it, ${writer} writes it` : `${everyone} reads and writes it`;
  }
  return null;                                  // a group's people ARE the point — see `peopleLine`
}

/** How many other people fold into "and N more" before the names stop. Two, because the founder's
 *  own line is `you, <names> and 2 more` — a first line that lists eight people is a roster, and the
 *  roster is one click away with everybody's role beside them. */
export const NAMES_SHOWN = 2;

/** WHO IS HERE, as names, with "you" first (#1634 rule 1).
 *
 *  A member nobody has written down has no name, and this never invents one out of their address —
 *  they are counted in the *and N more*, which is true, rather than rendered as a mailbox. */
export function peopleLine(members: readonly NamedMember[] | null, mySubject?: string | null): string | null {
  // THE ROSTER IS NOT ALWAYS READABLE, and for a reader of a group that is the design rather than a
  // failure (the members route is contributor+). Naming nobody is then the honest answer.
  if (!members) return "you and the other members";
  const others = members.filter((m) => m.subject !== mySubject);
  const iAmIn = members.length !== others.length;
  const named = others.map((m) => (m.name || "").trim()).filter(Boolean);
  const shown = named.slice(0, NAMES_SHOWN);
  const rest = others.length - shown.length;
  const parts = [...(iAmIn ? ["you"] : []), ...shown];
  if (!parts.length) return rest > 0 ? `${rest} member${rest === 1 ? "" : "s"}` : null;
  if (rest > 0) parts.push(`${rest} more`);
  if (parts.length === 1) return parts[0] === "you" ? "just you" : parts[0];
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** What a person calls this kind of place, in the first line. `group` is the server's word and
 *  `shared workspace` is what the panel has always shown for it (`kindLabel`); this is the same
 *  gloss in the middle of a sentence. */
export const placeWord = (kind: WorkspaceKind): string =>
  kind === "desk" ? "Your desk" : kind === "global" ? "Company layer" : "shared workspace";

export interface FrontPageFacts {
  kind: WorkspaceKind;
  name: string | null;
  pages: number | null;
  policies: string | null;
  company: string | null;
  adminFirstName?: string | null;
  members: readonly NamedMember[] | null;
  mySubject?: string | null;
  myRole?: Role | null;
  bound?: readonly BoundSeries[];
  profileFact?: boolean;
}

const join = (parts: (string | null | undefined)[]): string =>
  parts.map((p) => (p ?? "").trim()).filter(Boolean).join(" · ");

const pageCount = (n: number | null): string | null =>
  n === null ? null : `${n} page${n === 1 ? "" : "s"}`;

/** LINE ONE — where you are and who is here.
 *
 *  Three shapes, and the difference between them is not decoration: a desk and the company layer are
 *  answered by a RULE (who may read this), a shared workspace by PEOPLE (who is here). Putting the
 *  page count on the first two and the names on the third is that difference, in the order the
 *  founder wrote them. */
export function lineOne(f: FrontPageFacts): string {
  const visible = visibilitySentence(f.kind, f.policies,
    { company: f.company, adminFirstName: f.adminFirstName });
  if (f.kind === "desk") return join(["Your desk", pageCount(f.pages), visible]);
  if (f.kind === "global") return join(["Company layer", visible, pageCount(f.pages)]);
  return join([f.name || "This workspace", placeWord(f.kind), peopleLine(f.members, f.mySubject)]);
}

// ── line two: the last change, as a sentence ────────────────────────────────────────────────────

const NUMBER_WORDS = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
/** A count in words up to ten, digits past it — *five pages* is the founder's own form, and
 *  *seventeen pages* is a number pretending to be a word. */
export const countWord = (n: number): string => NUMBER_WORDS[n] || String(n);

/** What the file IS, when its folder says so. An ask is not a page — it is the conversation the
 *  product runs — and *the policies wizard ask* is what the founder called `asks/policies-wizard.md`
 *  in this issue. Everything else is a page, which is what everything else is. */
const noun = (path: string): string => (/^asks\//.test(path) ? "ask" : "page");

/** THE CHANGED THING (#1634 rule 2): one page by its title, several as a count.
 *
 *  `null` when the commit touched no page a person is shown — the sentence then ends at the time and
 *  the person, which is still true. A change to `.vexa/workspace.json` has no title to give. */
export function changedThing(change: Pick<LastChange, "pages" | "count">): string | null {
  const pages = change.pages ?? [];
  if (!pages.length) return null;
  if (pages.length === 1) {
    // THE ARTICLE IS NOT DOUBLED. Plenty of pages title themselves *the governing board*, and
    // "the the governing board page" is the sentence reading as a template with a slot in it.
    const title = pages[0].title.trim();
    const article = /^(the|a|an)\s/i.test(title) ? "" : "the ";
    return `${article}${title} ${noun(pages[0].path)}`;
  }
  return `${countWord(pages.length)} pages`;
}

/** WHEN, RELATIVELY — git's own `%cr` string, with one substitution.
 *
 *  Git says *23 hours ago* and *1 day ago*; a person says *yesterday*, and the founder's own line
 *  does. So the calendar-day difference (not a 24-hour window — 11pm and 1am are yesterday and
 *  today) replaces exactly that one case, and every other case is git's string read rather than
 *  retyped. Without a timestamp there is nothing to compute and the string stands. */
export function whenPhrase(change: Pick<LastChange, "when" | "ts">, now: number = Date.now()): string {
  const ts = Number(change.ts ?? 0);
  if (ts > 0) {
    const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    const days = Math.round((midnight(new Date(now)) - midnight(new Date(ts * 1000))) / 86_400_000);
    if (days === 1) return "yesterday";
  }
  return change.when;
}

/** WHO, AS A PERSON. Their own name; *you* when it was this reader; *someone* when nobody has
 *  written them down. Never an address — the server answers `null` rather than one, and this is the
 *  word that stands in its place. */
export function authorPhrase(change: Pick<LastChange, "kind" | "author">): string {
  if (change.kind === "you") return "you";
  return (change.author || "").trim() || "someone";
}

/** LINE TWO's first half — the last change, as a sentence. */
export function lastChangeSentence(change: LastChange | null, now?: number): string {
  if (!change) return "Nothing has been written here yet";
  const head = `Changed ${whenPhrase(change, now)} by ${authorPhrase(change)}`;
  const thing = changedThing(change);
  return thing ? `${head}: ${thing}` : head;
}

/** LINE TWO's second half — the one fact this KIND of place carries (#1634 rule 4): the policy
 *  profile on the company layer, the bound meeting series on a group. A desk carries neither, and a
 *  clause invented to fill the space would be the thing this issue removed. */
export function kindFact(f: FrontPageFacts): string | null {
  if (f.kind === "global") {
    const profile = policyProfile(f.policies);
    return profile ? `policies: ${profile} profile` : null;
  }
  if (f.kind === "group") {
    const series = (f.bound ?? []).find((b) => b.recurring) ?? (f.bound ?? [])[0];
    if (!series) return null;
    return series.recurring ? `bound to ${series.title}` : `from ${series.title}`;
  }
  return null;
}

export const lineTwo = (f: FrontPageFacts, change: LastChange | null, now?: number): string =>
  join([lastChangeSentence(change, now), kindFact(f)]);

// ── the acts this viewer may take ───────────────────────────────────────────────────────────────

/** An act on the strip. `id` is stable (it is what a test and a screenshot both name it by), `label`
 *  is what the person reads, `why` is the tooltip. */
export interface StripAct { id: StripActId; label: string; why: string }
export type StripActId = "policies" | "editor" | "member" | "sync" | "connect" | "history";

/** WHAT THIS VIEWER MAY DO HERE (#1634 rule 3), and nothing else.
 *
 *  A READER GETS ONE BUTTON. Not a greyed *Add a member*, not one that explains why it will refuse —
 *  none. A control whose only outcome is a 403 teaches a person that the product is broken rather
 *  than that they lack the role, which is the rule this panel has kept since #1623 and the reason
 *  `AttachRepo` was never offered where it could not work.
 *
 *  THE COMPANY LAYER HAS NO REPO BUTTON, for the same reason: it is mounted read-only into every
 *  worker and is not one of the attach flow's targets (#1628 established it, on the founder's own
 *  screen). Its acts are the two the administrator actually has — the policy decision and who else
 *  may write. */
export function stripActs(f: {
  kind: WorkspaceKind; owner: boolean; remote?: GitRemoteStatus | null;
}): StripAct[] {
  const history: StripAct = { id: "history", label: "History", why: "What has changed here, and everything else this workspace knows" };
  if (!f.owner) return [history];
  if (f.kind === "global") {
    return [
      { id: "policies", label: "Set up policies", why: "Five questions about your own risks, then a recommended policy with its reasoning" },
      { id: "editor", label: "Add an editor", why: "Ask the chat to give somebody write access to the company layer" },
      history,
    ];
  }
  const repo: StripAct = f.remote?.has_home
    ? { id: "sync", label: "Sync", why: "Ask the chat to sync this workspace with its GitHub home" }
    : { id: "connect", label: "Connect a repo", why: "Ask the chat to connect this workspace to a GitHub repository" };
  // A DESK HAS NO MEMBERS to add — it is one person's, and the sentence above says so.
  return f.kind === "desk" ? [repo, history] : [
    { id: "member", label: "Add a member", why: "Ask the chat who should join, and in which role" },
    repo, history,
  ];
}

/** WHAT THE ACT SAYS TO THE CHAT (#1632's principle: a button queues a same-target act, no forms).
 *
 *  Founder, 2026-09-06, pressing *Add a member…* and being handed `invite role must be one of
 *  ('contributor',)`: *"this add member should just ask chat to do that with mcp, asking their
 *  emails etc."* So a button never opens a dialog and never mints anything: it puts the act on the
 *  conversation, the agent asks for what it needs, confirms in one sentence, and does it.
 *
 *  THREE OF THE FIVE ARE TYPED INTENTS AND CARRY NO TEXT AT ALL. **Set up policies** is #1627's
 *  `policies_wizard` and **Add a member** is #1632's `member_add`, both of which name a KIND the
 *  server maps to an ask in `_global/asks/` — nothing here composes their sentence, for the reason
 *  `PoliciesAct` states: anyone able to make a client send an intent would otherwise be able to
 *  drive somebody else's agent. **History** is a disclosure and not a turn.
 *
 *  THE TWO THAT DO CARRY TEXT ARE ACTS WITH NO VERB BEHIND THEM YET, and each is honest about it:
 *
 *  · **Add an editor** is deliberately not `member_add`. `workspace_invite` REFUSES `_global`
 *    (#1632) because the company layer's editors are a named set in `POLICIES.md` and a membership
 *    record there would authorise nothing — so the act asks the chat to write that set, which is
 *    where the answer actually lives.
 *  · **Sync** and **Connect a repo** are #1623's controls stated as a conversation. The precise
 *    ones — sync now, pull, push, detach, and the attach dialog — are untouched and one click away
 *    in the GitHub section, which is where a person who wants a control rather than a sentence will
 *    look. */
export function actInstruction(id: StripActId, where: { workspace: string; name?: string | null }): string {
  const place = where.name ? `“${where.name}” (\`${where.workspace}\`)` : `\`${where.workspace}\``;
  // `member` is #1632's typed intent and composes nothing here — see the note above.
  if (id === "editor") {
    return `Add an editor to the company layer. Read \`_global/POLICIES.md\` first — the editors ` +
      `are a named set in that file, not a membership. Ask me whose address it is in ONE question, ` +
      `tell me in one sentence what an editor there may write, and only write the file if I say ` +
      `yes. No form: ask me here.`;
  }
  if (id === "sync") {
    return `Sync ${place} with its GitHub home: fetch, tell me in one line what is ahead and what ` +
      `is behind, and ask me before you push or pull anything.`;
  }
  if (id === "connect") {
    return `Connect ${place} to a GitHub repository. Ask me for the repository in ONE question — ` +
      `an existing one or a new one — say what will be pushed there, and only do it if I say yes.`;
  }
  return "";
}

/** What the person sees in the conversation when they press one — the act's own label and the place
 *  it was pressed on, never the instruction above. A bubble that renders the instruction puts words
 *  in the person's mouth they did not write (the rule `extend.ts` was built on). */
export const actDisplay = (act: StripAct, where: { workspace: string; name?: string | null }): string =>
  `${act.label}: ${where.name || where.workspace}`;
