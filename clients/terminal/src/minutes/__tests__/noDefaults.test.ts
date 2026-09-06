/** NO GREETING, NO FRESH-THREAD TEXT, NO DEFAULT BUTTONS — F36 + F37, founder rulings 2026-09-02.
 *
 *  He opened a rail holding four chats, three of which he had never made, each of them talking to
 *  him: a "Personal" row opened on *"I'm your agent here… paste a meeting link… tell me who you are
 *  and what you're accountable for"*; an "Organisation setup" row showing a card that asked *"What
 *  organisation are you? Just the name is enough — I'll research the rest and bring it back for your
 *  sign-off"*; and a "New chat" he had pressed `+` on and never typed in, offering *"A fresh thread
 *  in this project…"* and a button reading *"Create a group for daily meetings"*.
 *
 *  *"where is it coming from? i did not create this chat, and i do not like this text."*
 *  And on the org card specifically, once he saw it rendered from a row with no scaffold behind it:
 *  *"I explain this as stale code."*
 *
 *  ── WHY THIS TEST READS THE SOURCE ────────────────────────────────────────────────────────────
 *  The ruling is that these paths are **deleted, not made unreachable**. A behavioural test can only
 *  show that a string does not appear on the route it happens to drive; it passes just as happily
 *  while the string sits in a branch nobody currently enters, waiting for the next refactor to make
 *  it reachable again. Reading the files is the only assertion that matches what was actually ruled.
 *
 *  Each string below is quoted from the founder's own screenshots.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

/** Every `.ts`/`.tsx` under `src/`, tests excluded — a deleted string may legitimately be QUOTED in
 *  a test (this file quotes all of them) while being absent from everything that renders. */
function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name === "node_modules" || name === "__tests__" || name === ".next") continue;
      sourceFiles(p, out);
    } else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name)) {
      out.push(p);
    }
  }
  return out;
}

const SRC = join(process.cwd(), "src");
const FILES = sourceFiles(SRC);
/** Comments are where the deletions are EXPLAINED, and an explanation has to be allowed to name the
 *  thing it deleted or the record of why goes with it. So the search is over code only. */
function code(path: string): string {
  return readFileSync(path, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")     // block comments, JSDoc included
    .replace(/(^|[^:])\/\/.*$/gm, "$1");  // line comments (not the `//` of a URL)
}
const CODE = FILES.map((f) => ({ f, src: code(f) }));

describe("F36/F37 — the strings the founder saw are gone from the source", () => {
  const REMOVED: [string, string][] = [
    ["I'm your agent here", "the home greeting — a chat about nothing, greeting"],
    ["paste a meeting link", "…and the instruction inside it"],
    ["what you're accountable for", "…and the question inside it"],
    ["A fresh thread in this project", "the empty state of a plain `+` chat"],
    ["What organisation are you?", "the pre-scaffold admin card"],
    ["research the rest", "…and its subline, promising a step that does not exist"],
    ["Create a group for daily meetings", "the standing suggestion that padded the chip row"],
    ["I'm your knowledge agent", "the personal onboarding greeting the deleted seed wrote"],
    ["The organisation", "the `_global` tab's friendly stand-in for its own name"],
    ["Organisation setup", "the planted row's label"],
  ];

  for (const [needle, what] of REMOVED) {
    it(`"${needle}" — ${what}`, () => {
      const hits = CODE.filter(({ src }) => src.includes(needle)).map(({ f }) => f.slice(SRC.length + 1));
      expect(hits).toEqual([]);
    });
  }
});

describe("F36/F37 — and so are the mechanisms that carried them", () => {
  const GONE: [string, string][] = [
    ["ONBOARDING_SEED_EVENT", "the event that wrote a cached greeting into an empty chat"],
    ["COMPANY_LAYER_EVENT", "the announcement that told the rail when to plant its rows"],
    ["companyLayerHint", "the cache that decided whether to plant them"],
    ["setCompanyLayerHint", "…and its two writers"],
    ["seedChats", "the seeds themselves"],
    ["ensureSeeds", "…and the call that restored a missing one on every load"],
    ["GLOBAL_SETUP_GROUNDING", "the pre-scaffold admin grounding, attached to an `org-setup` session"],
    ["GROUP_PROPOSAL", "the hardcoded chip"],
    ["presetOwnsOpening", "the flag that kept the greeting from beating a preset — no greeting, no race"],
  ];

  for (const [symbol, what] of GONE) {
    it(`${symbol} — ${what}`, () => {
      const hits = CODE.filter(({ src }) => src.includes(symbol)).map(({ f }) => f.slice(SRC.length + 1));
      expect(hits).toEqual([]);
    });
  }

  it("no `org-setup` session branch survives anywhere — the id it keyed on cannot be minted", () => {
    // The rail's seeding was the ONLY thing that ever produced an `org-setup` session, so every
    // branch keyed on it became unreachable the moment the seeding went. Unreachable is not the
    // standard: "I explain this as stale code."
    const hits = CODE.filter(({ src }) => src.includes('startsWith("org-setup")')).map(({ f }) => f);
    expect(hits).toEqual([]);
  });
});

/** F97 — A URL MUST NOT BE ABLE TO DRIVE THE AGENT'S FIRST TURN (decisions 13 / 18).
 *
 *  The `?s=` scaffold path obeyed this; the surviving `?ask=` hand link did not. The terminal read
 *  the preset itself and substituted `?meeting=` and `?ws=` straight into the composed opening
 *  (`MinutesShell.tsx:1012, 1039, 1042`), so a crafted `/?ask=prep&meeting=<payload>` put
 *  attacker-chosen words into the first thing the model was asked.
 *
 *  READ THE SOURCE, for the same reason the file above does: the ruling is that client-side
 *  composition is DELETED, not merely unreachable. A behavioural test proves one route is clean
 *  today; it says nothing about a substitution sitting in a branch waiting for the next refactor.
 */
describe("F97 — the client composes no prompt text", () => {
  const src = sourceFiles(join(__dirname, "..", ".."));
  const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");

  it("the shell substitutes NOTHING into an opening — no `{{token}}` replacement anywhere in it", () => {
    // the whole `.replace(/\{\{\s*meeting\s*\}\}/g, ref)` family is gone
    expect(shell).not.toMatch(/\{\{\s*\\s\*/);
    expect(shell).not.toMatch(/replace\(\s*\/\\\{\\\{/);
    for (const token of ["meeting", "ws", "title", "when", "state", "today", "workspace"]) {
      expect(shell).not.toContain(`{{${token}}}`);
    }
  });

  it("no source file builds an opening out of the URL's `?meeting=` or `?ws=`", () => {
    // `intent.ws` was forwarded into the mount set and `intent.meeting` into the text; neither may
    // reach a prompt. The hand link now sends two NAMES to the server and takes back an id.
    for (const f of src) {
      const body = readFileSync(f, "utf8");
      expect(body, `${f} substitutes a {{token}} client-side`).not.toMatch(/replace\([^)]*\{\{/);
    }
  });

  it("the hand link MINTS instead — it posts names, never text", () => {
    expect(shell).toContain("/api/scaffolds/hand");
    // exactly two fields go up, and both are names
    expect(shell).toMatch(/JSON\.stringify\(\{\s*preset:[^}]*meeting:[^}]*\}\)/);
    // and it hands off to the one composition path rather than opening a chat itself
    expect(shell).toMatch(/window\.location\.replace\(`\/\?s=/);
  });

  it("`localScaffold` — the client-side scaffold constructor — is gone from the shell", () => {
    // it existed only to let the hand link compose a record in the browser
    expect(shell).not.toContain("localScaffold");
  });
});
