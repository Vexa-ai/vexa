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
 *  So the front page is a HEADER — an eyebrow, the title, and two grey rows under it — and every
 *  function in this file exists to make one clause of it (#1634's design spec, 2026-09-06 22:15Z:
 *  *"no one will read it and no one will be happy about this, make it a proper design"*):
 *
 *      Company layer                                                          ← the eyebrow (kind)
 *      Acme                                                                   ← the title
 *      (JS) everyone at Acme reads it, Jane writes it · 25 pages · ⚑ policies: default profile
 *                                          [Set up policies] [Add an editor] [🕘]
 *      🕐 Jane Smith changed the policies wizard ask 14 minutes ago
 *      ──────────────────────────────────────────────────────────────────────  ← the hairline
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
 *  3. **An address is never a name — and neither is a pronoun** (Vexa-ai/vexa#1642). The server
 *     resolves the person FROM their address (`front_page.display_name`: their desk's own page, the
 *     company directory, the people record, the identity note, and finally the address's local part
 *     read as a name), so the answer is a name a reader can check. `null` survives only where there
 *     was nothing at all to read, and it renders as NO CLAUSE rather than as *someone*: the founder
 *     met *Changed 60 minutes ago by someone* on the instance whose every commit is authored by
 *     him, which told him the product does not know who works there.
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
    if (!adminOnly) return `${everyone} reads and writes it`;
    // **THE WRITER IS A PERSON OR NOBODY — never the words *the admin*** (Vexa-ai/vexa#1642).
    // That was the fallback, and the founder met it on his own instance: *"everyone at Vexa reads
    // it, THE ADMIN writes it"*, where the administrator is himself, his desk holds his person
    // page and every commit in `_global` is authored by his address. A role word in a name's slot
    // reads as a template nobody filled in. `/api/people/admin` resolves the name from the layer's
    // own acceptances now; where it genuinely cannot, the clause is DROPPED rather than filled with
    // a placeholder — *everyone at Acme reads it* is true, and who may write it is one click away
    // in the details, stated as the rule it is.
    const writer = (who.adminFirstName || "").trim();
    return writer ? `${everyone} reads it, ${writer} writes it` : `${everyone} reads it`;
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

/** THE EYEBROW (#1634's design spec, point 1) — the kind, 12px and muted, above the title.
 *
 *  The same three words as `placeWord`, capitalised for the position they are in: an eyebrow is a
 *  label rather than a clause, so *Shared workspace* opens it and the people sentence underneath no
 *  longer has to carry the kind at all. That subtraction is the whole reason the eyebrow exists —
 *  `Pilot · shared workspace · you, Jane Smith and 2 more` says the kind in the middle of a
 *  sentence about people, where it reads as one more fact in a list. */
export const eyebrow = (kind: WorkspaceKind): string =>
  kind === "desk" ? "Your desk" : kind === "global" ? "Company layer" : "Shared workspace";

/** THE PAGE'S OWN TITLE, and the body with that heading taken off it.
 *
 *  The header owns the title (#1634's spec, point 2: *"the page H1 the README already has; the
 *  header does not repeat it"*), so the README's first heading is LIFTED rather than duplicated —
 *  a title in the header and the same words again two lines down is the shape a person reads as a
 *  bug. A README with no heading leaves `title: null` and the header falls back to the workspace's
 *  own name, which is the spec's other half. */
export function splitLeadingH1(md: string | null): { title: string | null; body: string } {
  const text = String(md ?? "");
  const lines = text.split("\n");
  let i = 0;
  // front matter, if the page opens with it — a title under it is still the page's first heading
  if (lines[0]?.trim() === "---") {
    const end = lines.indexOf("---", 1);
    if (end === -1) return { title: null, body: text };
    i = end + 1;
  }
  const from = i;
  while (i < lines.length && !lines[i].trim()) i++;
  const m = /^#\s+(.+?)\s*$/.exec(lines[i] ?? "");
  if (!m) return { title: null, body: text };
  const rest = [...lines.slice(0, from), ...lines.slice(i + 1)];
  while (rest.length && !rest[0].trim()) rest.shift();
  return { title: m[1].trim(), body: rest.join("\n") };
}

/** WHOSE FACE IS ON THIS PAGE — the avatars of the people the first line is about (#1634's spec,
 *  point 3), in the order the sentence names them.
 *
 *  A desk is one person's and the company layer has one writer, so both show exactly one circle;
 *  a shared workspace shows you first and then whoever is written down, three at most, because the
 *  fourth is what *and N more* is for. Somebody nobody has written down has no initials and is
 *  simply not drawn — an avatar reading `?` is a hole with a border around it. */
export interface Avatar { key: string; name: string; you: boolean }
export const AVATARS_SHOWN = 3;
export function avatarPeople(f: FrontPageFacts): Avatar[] {
  const mine = (f.myName || "").trim();
  if (f.kind === "desk") return mine ? [{ key: "me", name: mine, you: true }] : [];
  if (f.kind === "global") {
    const admin = (f.adminName || "").trim();
    return admin ? [{ key: "admin", name: admin, you: admin === mine }] : [];
  }
  const others = (f.members ?? []).filter((m) => m.subject !== f.mySubject);
  const iAmIn = (f.members ?? []).length !== others.length;
  const rows: Avatar[] = iAmIn && mine ? [{ key: "me", name: mine, you: true }] : [];
  for (const m of others) {
    const name = (m.name || "").trim();
    if (name) rows.push({ key: m.subject, name, you: false });
  }
  return rows.slice(0, AVATARS_SHOWN);
}

/** A NAME, AS TWO LETTERS. The first letter of the first two words — *Jane Smith* is JS and
 *  *Dmitry* is D, which is what an avatar of a person with one name should be rather than a padded
 *  two-letter guess. Non-letters are dropped, so a hyphenated or accented name still reads. */
export function initialsOf(name: string): string {
  const words = String(name ?? "").split(/[\s._-]+/).map((w) => w.replace(/[^\p{L}\p{N}]/gu, "")).filter(Boolean);
  return words.slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "";
}

export interface FrontPageFacts {
  kind: WorkspaceKind;
  name: string | null;
  pages: number | null;
  policies: string | null;
  company: string | null;
  adminFirstName?: string | null;
  /** the administrator's whole name — the avatar wants both initials, the sentence wants one word */
  adminName?: string | null;
  /** the reader's own name, for their avatar and for the "you" the sentence already says */
  myName?: string | null;
  members: readonly NamedMember[] | null;
  mySubject?: string | null;
  myRole?: Role | null;
  bound?: readonly BoundSeries[];
  profileFact?: boolean;
}

const join = (parts: (string | null | undefined)[]): string =>
  parts.map((p) => (p ?? "").trim()).filter(Boolean).join(" · ");

export const pageCount = (n: number | null): string | null =>
  n === null ? null : `${n} page${n === 1 ? "" : "s"}`;

/** WHO IS HERE, in the one clause the people row is built around.
 *
 *  Three shapes, and the difference between them is not decoration: a desk and the company layer are
 *  answered by a RULE (who may read this), a shared workspace by PEOPLE (who is here).
 *
 *  It no longer carries the KIND or the workspace's NAME, and that is #1634's design spec rather
 *  than a subtraction of meaning: the kind is the eyebrow and the name is the title, both directly
 *  above this row. `Pilot · shared workspace · you, Jane Smith and 2 more` was three answers in a
 *  row with no sentence among them. */
export function peopleClause(f: FrontPageFacts): string | null {
  if (f.kind === "group") return peopleLine(f.members, f.mySubject);
  return visibilitySentence(f.kind, f.policies,
    { company: f.company, adminFirstName: f.adminFirstName });
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

/** WHO, AS A PERSON. Their own name, or *you* when it was this reader.
 *
 *  **`null`, NEVER *someone*** (Vexa-ai/vexa#1642). That word was what this returned when the
 *  server's chain answered nothing, and the founder met it on the one instance where the person
 *  certainly exists — the server now resolves the name from the ADDRESS and falls back to that
 *  address read as a name, so `null` here means there was genuinely nothing to read. The sentence
 *  then names nobody rather than naming a pronoun: *Changed 2 hours ago* is true and *someone*
 *  tells a reader the product does not know who works here. */
export function authorPhrase(change: Pick<LastChange, "kind" | "author">): string | null {
  if (change.kind === "you") return "you";
  return (change.author || "").trim() || null;
}

/** THE LAST-CHANGE ROW, IN ITS PARTS (#1634's design spec, point 4: *"a clock icon, `<Name> changed
 *  <the page title> <relative time>` — the title a quiet link that opens the page"*).
 *
 *  Parts rather than a string, because one of them is a LINK and the rest is not: the changed page
 *  opens where it lives, and a sentence assembled in the panel could only underline the whole of
 *  itself. `page` is null when several changed (the count is in `thing` instead) or when the commit
 *  touched no page a person is shown. */
export interface LastChangeParts {
  who: string | null;
  thing: string | null;
  page: { path: string; title: string } | null;
  when: string;
}
export function lastChangeParts(change: LastChange, now?: number): LastChangeParts {
  const pages = change.pages ?? [];
  return {
    who: authorPhrase(change),
    thing: changedThing(change),
    page: pages.length === 1 ? pages[0] : null,
    when: whenPhrase(change, now),
  };
}

/** LINE TWO's first half — the last change, as a sentence, in the founder's own order.
 *
 *  *Jane Smith changed the governing board page 2 hours ago*, not *Changed 2 hours ago by Jane
 *  Smith: the governing board page*. The person is the subject of the sentence because they are
 *  what the row is about; the colon form was a log entry with words around it. */
export function lastChangeSentence(change: LastChange | null, now?: number): string {
  if (!change) return "Nothing written here yet";
  const { who, thing, when } = lastChangeParts(change, now);
  const subject = who === "you" ? "You" : who;
  const tail = [thing, when].filter(Boolean).join(" ");
  return subject ? `${subject} changed ${tail}` : `Changed ${tail}`;
}

/** THE PILL — the one fact this KIND of place carries (#1634 rule 4, and point 3 of its design
 *  spec, which moved it up beside the page count): the policy profile on the company layer, the
 *  bound meeting series on a group. A desk carries neither, and a clause invented to fill the space
 *  would be the thing this issue removed. */
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
