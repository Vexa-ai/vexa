"use client";
/** THE WORKSPACE'S FRONT PAGE — TWO SENTENCES and the acts this viewer may take, between a
 *  workspace README's title and its prose. Everything else is behind one disclosure.
 *
 *  Founder, 2026-09-06, on what #1628 had just sized down to the header — *"Company layer · 25 pages
 *  · _global: asks/policies-wizard.md … · dmitry@vexa.ai · 14 minutes ago · Everyone reads, the
 *  admin writes · no repo attached · 10+ commits"*: **"what about this one? never spoke about how to
 *  make it right, helpful and nice."** (Vexa-ai/vexa#1634.) The size was fixed; the content was a
 *  list of repository facts — a path, an address, a commit count, a repo state. **A person needs a
 *  sentence about a place.**
 *
 *  So the strip is two lines and a few buttons:
 *
 *      Company layer · everyone at Acme reads it, Jane writes it · 25 pages
 *      Changed 14 minutes ago by Jane Smith: the policies wizard ask · policies: default profile
 *          [Set up policies] [Add an editor] [History]
 *
 *  LINE ONE is where you are and who is here — people as NAMES with "you" first, or, where people
 *  are not the point (a desk, the company layer), the visibility sentence derived from `POLICIES.md`
 *  and the kind. LINE TWO is the last change as a sentence: the changed thing by its TITLE, the
 *  author by their NAME, a relative time — none of which is in a git log, which is why the server
 *  resolves all three (`control_plane/front_page.py`). The words themselves are decided by pure
 *  functions in `./workspaceFrontPage`, so the three lines the founder wrote are held by tests
 *  against fixture data rather than by a screenshot.
 *
 *  THE BUTTONS ARE ACTS, AND AN ACT IS A CONVERSATION (#1632's principle, founder: *"this add
 *  member should just ask chat to do that with mcp, asking their emails etc."*). Each queues a
 *  same-target act on the open chat; none opens a form. A READER sees exactly one of them —
 *  History — because a control whose only outcome is a 403 teaches a person that the product is
 *  broken rather than that they lack the role.
 *
 *  NOTHING ELSE IS ON THE STRIP (#1634 rule 3): no commit count, no *no repo attached*, no address,
 *  no path. All of it is still true and still one click away — behind **History**, the one
 *  disclosure at the end of line two. The 12vh cap stays on the strip for the reason #1628 put it
 *  there.
 *
 *  AND NOTHING ELSE IS IN THE DETAILS EITHER (Vexa-ai/vexa#1642, second look). #1628's six SUMMARIES
 *  — *Company layer · 30 pages · \_global: .claude/mcp.json… · you · 49 minutes ago · Everyone reads,
 *  the admin writes · no repo attached · 10+ commits* — were still the first thing under the
 *  disclosure, which is the exact line the founder rejected, printed one level down. They are gone.
 *  What is behind the disclosure is THREE SECTIONS and no summary of them: **the people here with
 *  their roles**, **the repo state**, **the history list with its scope toggle**. The kind, the page
 *  count and the last change are not repeated there because the two sentences above already say
 *  them, and a line that answers a question the header answered is the strip again.
 *
 *  NOTHING IS REMEMBERED. The disclosure opens closed, every time, on every workspace. #1628 asked
 *  for the open section to be remembered per workspace and it was — in `vexa.wsreadme.open:<slug>`,
 *  which is how the founder's browser arrived at `_global` with the details ALREADY OPEN from a build
 *  three shas earlier. A posture that may not default the disclosure open has no effect left to have,
 *  so it is not stored: the key is retired rather than versioned, this panel reads no storage at all,
 *  and any value an earlier build wrote is inert by construction rather than by a version check.
 *
 *  KEYBOARD. The strip's acts are ordinary buttons — there are at most three and each is a different
 *  thing to do — and the details are one region under the one that opened them.
 *
 *  THE FOUR RULES IT IS STILL BUILT ON, unchanged from #1623 and each readable against the code:
 *
 *  1. **A reader sees data and history, no controls.** Not disabled controls, not controls that
 *     explain why they will not work — none. A control whose only outcome is a 403 teaches a person
 *     that the product is broken rather than that they lack the role (`AttachRepo`'s own lesson,
 *     three files over). `facts.owner` is the switch, and the server decides again on every act.
 *  2. **Every control is a confirmed act** — one click arms, a second commits, and the armed state
 *     says what will happen in words — **except the three membership controls, which are now plain
 *     buttons** (founder, 2026-09-06, Vexa-ai/vexa#1632). *"So we do not have to create UI here —
 *     button to trigger the chat."* Add a member, Change role and Remove queue an act on the
 *     workspace's chat; the agent asks for the addresses and the role in one question and confirms
 *     in one sentence a person can answer in words. The confirmation did not disappear, it MOVED —
 *     and arming a button that only opens a conversation would be a confirmation of the question
 *     rather than of the act. Rule 2 is unchanged for push, pull and detach, which still fire from
 *     here.
 *  3. **Every write is a commit or a receipt.** Push, pull and detach answer here with the remote
 *     and branch they touched. Membership and role changes still commit into the workspace's own
 *     `policy/members.json` server-side — but the write now comes from the agent's own verbs
 *     (`workspace_invite` · `workspace_membership`), so its receipt is the agent's sentence in the
 *     chat and this panel renders none.
 *  4. **Nothing is guessed.** Every fact is nullable; a read that failed says so in its own section
 *     rather than rendering as a zero — and, since #1628, *no repo attached* is a STATE and not a
 *     failure: the red line is reserved for a read that actually broke, and it names what broke.
 */
import {
  useCallback, useEffect, useState,
  type CSSProperties, type ReactNode,
} from "react";
import { Icon } from "../ui-kit";
import { presentError } from "../surfaces/apiClient";
import {
  detachWorkspaceRemote, gitRemoteStatus, pullWorkspace, pushWorkspace,
  readWorkspaceGitDiff,
  type GitCommit, type WorkspaceMember,
} from "../surfaces/workspaceApi";
import { ASK_CHAT_EVENT } from "../canvas/actions";
import { AttachRepo } from "./AttachRepo";
import { postIntent } from "./extend";
import { POLICIES_PATH, POLICIES_WORKSPACE } from "./PoliciesAct";
import { useOpenEntity } from "../ui-kit/docRefs";
import { surface, type as ty } from "./tokens";
import {
  actDisplay, actInstruction, avatarPeople, eyebrow, initialsOf, kindFact, lastChangeParts,
  lastChangeSentence, pageCount, peopleClause, stripActs,
  type StripAct, type FrontPageFacts,
} from "./workspaceFrontPage";
import {
  DESK_SLUG, HISTORY_PAGE, loadHistory, loadWorkspaceFacts, roleLabel, type WorkspaceFacts,
} from "./workspaceReadme";

/** THE STRIP IS NOT A CARD ANY MORE (#1634 rule 5: *"grey, small, sentence-like — the people-and-
 *  last-edit line under a shared document's title. Body starts right under."*). A bordered panel
 *  says "here is a component"; two grey lines and a hairline say "here is where you are", which is
 *  what the founder asked for. The DETAILS keep the card, because they are a panel. */
const box: CSSProperties = {
  padding: "0 0 9px", marginBottom: 13, borderBottom: "1px solid var(--line)",
};
const detailsBox: CSSProperties = {
  border: "1px solid var(--line)", borderRadius: 8, background: surface.raised,
  padding: "8px 10px", margin: "0 0 16px",
};
/** THE HEIGHT RULE, as a number the code and its test share. 12% of the viewport, which is inside
 *  the founder's "1/8 screen at max" with the rounding in the reader's favour. */
export const STRIP_MAX_VH = 12;
const stripS: CSSProperties = {
  display: "flex", flexDirection: "column", alignItems: "stretch", gap: 3, minWidth: 0,
};
/** WHAT THE CAP IS MEASURED AGAINST — the two grey rows, and not the eyebrow and title above them.
 *
 *  #1628's ruling (*"the workspace panel should take only the header, 1/8 screen at max"*) is about
 *  the FURNITURE this panel adds to a page. The title is not furniture: it is the README's own first
 *  heading, which was already on the page as the body's `# `, and #1634's design spec moved it up
 *  here rather than adding it — so counting it against the cap would shrink the two rows to pay for
 *  a line the reader already had. Measured on the real 384px panel the rows come to ~100px against
 *  a 108px cap on a 900px viewport, which is what this split is worth: without it the last-change
 *  sentence was clipped out of reach on the founder's own screen.
 *
 *  `auto` rather than `hidden` is this file's own rule 1 applied to the cap itself: a control a
 *  person can see the top of and cannot click is the one thing this panel refuses to render. */
const rowsS: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 3, minWidth: 0,
  maxHeight: `${STRIP_MAX_VH}vh`, overflowY: "auto", overflowX: "hidden",
};
const sectionS: CSSProperties = { borderTop: "1px solid var(--line)", marginTop: 8, paddingTop: 8 };
const firstSectionS: CSSProperties = { marginTop: 0, paddingTop: 0 };
/** A SECTION'S NAME IS A LABEL, NEVER AN ANSWER. *People* · *Repo* · *History* — 12px, muted, the
 *  quietest thing in the panel. The moment one of them reads `no repo attached` or `10+ commits` it
 *  has become #1628's summary strip again, which is the line the founder rejected twice. */
const sectionNameS: CSSProperties = {
  ...ty.chip, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em",
  textTransform: "uppercase", margin: "0 0 5px",
};
/** A sentence, not a row of chips: the size and colour of the line under a shared document's
 *  title, and it WRAPS — a sentence that ellipsizes is a sentence with its ending taken away.
 *
 *  13px and `--t2` are the design spec's own values for both rows (#1634, 22:15Z), taken from the
 *  type scale rather than typed as numbers: `ty.body` IS 13px there, so a change to the shell's
 *  scale moves this with it. */
const lineS: CSSProperties = { ...ty.body, color: "var(--t2)", lineHeight: 1.55, minWidth: 0 };
/** THE EYEBROW — 12px, muted, sentence case. `ty.chip` is the scale's 12px face; the colour is the
 *  quietest of the three text tokens, in both themes, because an eyebrow is a label and the title
 *  under it is the thing. */
const eyebrowS: CSSProperties = { ...ty.chip, color: "var(--t3)", lineHeight: 1.5 };
/** THE TITLE. The page's own H1, lifted out of the body so it is not printed twice. */
const titleS: CSSProperties = {
  fontFamily: "var(--sans)", fontSize: 19, fontWeight: 600, letterSpacing: "-0.01em",
  color: "var(--t1)", margin: "1px 0 5px", lineHeight: 1.25, wordBreak: "break-word",
};
/** The two grey rows: a fixed thing on the left (the faces, the clock), the sentence, and — on the
 *  people row — the acts at the right edge.
 *
 *  THE SENTENCE, THE COUNT AND THE PILL ARE ONE INLINE FLOW, not three flex items. As three they
 *  each began a new LINE the moment the first of them wrapped, and on the real 384px panel that
 *  made the people row 80px of four stacked fragments. As one flow they wrap like the sentence they
 *  are. `flex: 1 1 180px` lets the acts sit beside them while they fit and drop to their own line
 *  when they do not. */
const rowFlow: CSSProperties = {
  display: "flex", alignItems: "flex-start", gap: "4px 8px", flexWrap: "wrap", minWidth: 0,
};
const saidS: CSSProperties = { flex: "1 1 180px", minWidth: 0 };
/** ONE FACE — 22px, initials, `-6px` overlap, a ring in the surface colour so the stack reads as
 *  cards rather than as a smear (#1634's design spec, point 3). */
const avatarS: CSSProperties = {
  width: 22, height: 22, borderRadius: "50%", flex: "none",
  display: "inline-flex", alignItems: "center", justifyContent: "center",
  fontFamily: "var(--sans)", fontSize: 9.5, fontWeight: 600, letterSpacing: ".02em",
  background: surface.raisedHi, color: "var(--t2)",
  border: "1px solid var(--line)", boxShadow: "0 0 0 2px var(--sidebar)",
  marginLeft: -6, position: "relative",
};
/** THE KIND PILL — 12px, a hairline, an outline icon. One per kind, or none. */
const pillS: CSSProperties = {
  ...ty.chip, display: "inline-flex", alignItems: "center", gap: 4, flex: "none",
  padding: "0 7px", borderRadius: 999, lineHeight: 1.7,
  border: "1px solid var(--line)", color: "var(--t3)", background: "transparent",
};
/** AN ACT — a small secondary button: 26px high, 12px text, quiet until it is hovered. */
const actS: CSSProperties = {
  ...ty.chip, flex: "none", height: 26, padding: "0 9px", borderRadius: 7, cursor: "pointer",
  display: "inline-flex", alignItems: "center", gap: 5, whiteSpace: "nowrap",
  border: "1px solid var(--line)", background: surface.raised, color: "var(--t2)",
};
/** …and the last one is the icon alone, with its name carried by `aria-label` and the tooltip. */
const iconActS: CSSProperties = { ...actS, width: 26, padding: 0, justifyContent: "center" };
/** THE LOADING STATE — the two rows as skeleton bars, so the header does not jump when they arrive
 *  and nothing false is said in the meantime (#1634's design spec, "States"). */
const barS = (w: number | string): CSSProperties => ({
  display: "block", height: 9, width: w, borderRadius: 5, background: surface.raisedHi,
});
const rowS: CSSProperties = { display: "flex", alignItems: "baseline", gap: 8, minWidth: 0, lineHeight: 1.5 };
const keyS: CSSProperties = { ...ty.meta, flex: "none", width: 74 };
const valS: CSSProperties = { ...ty.body, color: "var(--t1)", flex: "1 1 0%", minWidth: 0, wordBreak: "break-word" };
const btnS: CSSProperties = {
  ...ty.chip, flex: "none", padding: "2px 8px", borderRadius: 6, cursor: "pointer",
  border: "1px solid var(--line)", background: surface.raisedHi, color: "var(--t2)",
};
const dangerS: CSSProperties = { ...btnS, borderColor: "var(--danger)", color: "var(--danger)" };
/** A QUIET LINK — the changed page's title in the last-change row. Not the accent colour and not a
 *  full underline: it sits inside a grey sentence, so it is the sentence's own colour with a
 *  hairline under it (#1634's design spec, point 4), and it only warms up on hover. */
const quietLink: CSSProperties = {
  font: "inherit", color: "var(--t1)", background: "transparent", border: "none", padding: 0,
  cursor: "pointer", textDecoration: "underline", textDecorationThickness: "0.5px",
  textDecorationColor: "var(--line2)", textUnderlineOffset: 3,
};
const linkBtn: CSSProperties = {
  ...ty.meta, background: "transparent", border: "none", padding: 0, color: "var(--accent)",
  cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2,
};
function Fact(p: { k: string; name: string; children: ReactNode }) {
  return (
    <div style={rowS} data-ws-fact={p.k}>
      <span style={keyS}>{p.name}</span>
      <span style={valS}>{p.children}</span>
    </div>
  );
}

/** AN ACT IS ARMED, THEN COMMITTED. The armed state carries the SENTENCE — what is about to happen,
 *  to whom — because "Confirm" on its own is a button that asks a person to remember what they
 *  clicked. `busy` disables it rather than hiding it, so nothing moves under the cursor. */
function Act(p: {
  id: string; label: string; sentence: string; danger?: boolean; busy: boolean;
  armed: string | null; onArm: (id: string | null) => void; onRun: () => void;
}) {
  if (p.armed === p.id) {
    return (
      <span data-ws-confirm={p.id} style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
        <span style={{ ...ty.meta, color: "var(--t2)" }}>{p.sentence}</span>
        <button data-ws-act-confirm={p.id} disabled={p.busy} onClick={p.onRun}
          style={{ ...(p.danger ? dangerS : btnS), opacity: p.busy ? 0.55 : 1 }}>
          {p.busy ? "…" : "Confirm"}
        </button>
        <button data-ws-act-cancel={p.id} onClick={() => p.onArm(null)} style={{ ...btnS, border: "none", background: "transparent" }}>Cancel</button>
      </span>
    );
  }
  return (
    <button data-ws-act={p.id} onClick={() => p.onArm(p.id)} style={p.danger ? dangerS : btnS}>{p.label}</button>
  );
}

/** ONE COMMIT, AND THE FILES IT TOUCHED. The second line is not decoration: a turn-commit's message
 *  names the file the turn was ABOUT while the commit itself touches several, so under *this page
 *  only* the list read as unfiltered — `_global`'s README history is full of commits whose message
 *  says `MISSING.md, OBJECTIVES.md +13`. The files are what the filter actually matched, and git
 *  narrows them to the pathspec, so the row can be checked against the claim above it. */
function CommitRow(p: { c: GitCommit; open: boolean; diff: string | null; onOpen: () => void }) {
  const files = p.c.files ?? [];
  return (
    <div data-ws-commit={p.c.sha} style={{ marginBottom: 3 }}>
      <button onClick={p.onOpen} title={`${files.length} file(s)`}
        style={{ ...ty.body, display: "block", width: "100%", textAlign: "left",
                 background: p.open ? surface.raisedHi : "transparent", border: "none",
                 borderRadius: 6, padding: "3px 5px", cursor: "pointer", color: "var(--t1)" }}>
        <span style={{ display: "flex", gap: 7, alignItems: "baseline", minWidth: 0 }}>
          <span style={{ ...ty.mono, flex: "none" }}>{p.c.sha}</span>
          <span style={{ flex: "1 1 0%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.c.msg}</span>
          <span style={{ ...ty.meta, flex: "none" }}>{p.c.author ?? ""} · {p.c.when}</span>
        </span>
        {files.length > 0 && (
          <span data-ws-commit-files style={{ ...ty.meta, display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {files.slice(0, 3).join(", ")}{files.length > 3 ? ` +${files.length - 3}` : ""}
          </span>
        )}
      </button>
      {p.open && (
        <pre data-ws-diff style={{ ...ty.mono, whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "3px 0 8px",
                                   padding: 8, borderRadius: 6, background: "var(--bg)", border: "1px solid var(--line)",
                                   color: "var(--t2)", maxHeight: 260, overflow: "auto" }}>
          {p.diff ?? "…"}
        </pre>
      )}
    </div>
  );
}

/** ONE SECTION — its name, then the thing itself. The first carries no rule above it: a hairline
 *  under the disclosure's own border is a line drawn twice. */
function Section(p: { id: string; name: string; first?: boolean; children: ReactNode }) {
  return (
    <section data-ws-section={p.id} id={`ws-section-${p.id}`}
      style={p.first ? { ...sectionS, ...firstSectionS } : sectionS}>
      <h2 data-ws-section-name style={sectionNameS}>{p.name}</h2>
      {p.children}
    </section>
  );
}

/** THE FACES, overlapping, with the sentence's own people in the sentence's own order. `aria-hidden`
 *  on the whole stack: every name in it is already in the row beside it (or in *and N more*), and a
 *  screen reader reading "J S" before hearing "you, Jane Smith and 2 more" is being told the same
 *  thing twice, badly. */
function Avatars(p: { people: { key: string; name: string; you: boolean }[] }) {
  if (!p.people.length) return null;
  return (
    <span data-ws-avatars aria-hidden style={{ display: "inline-flex", flex: "none", paddingLeft: 6, alignSelf: "center" }}>
      {p.people.map((a) => (
        <span key={a.key} data-ws-avatar={a.key} title={a.name} style={avatarS}>{initialsOf(a.name)}</span>
      ))}
    </span>
  );
}

export function WorkspaceReadmePanel(p: { slug?: string; path: string; title?: string | null }) {
  const [facts, setFacts] = useState<WorkspaceFacts | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [commits, setCommits] = useState<GitCommit[] | null>(null);
  const [shown, setShown] = useState(HISTORY_PAGE);
  const [thisPage, setThisPage] = useState(false);
  // THE ONE DISCLOSURE (#1634 rule 6). The three sections live under it; History is the button that
  // opens it. **It starts closed, always** (Vexa-ai/vexa#1642): the front page is two sentences, and
  // a stored posture that reopened it is how the founder met this panel already open on `_global`.
  const [details, setDetails] = useState(false);
  const [openSha, setOpenSha] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);   // the receipt of the last act
  const [attaching, setAttaching] = useState(false);
  // THE CHANGED PAGE OPENS WHERE IT LIVES. The same callback every link inside a document uses, so
  // the title in the last-change row behaves exactly like a link in the prose under it — including
  // in-place navigation with the pane's own back/forward, which a bespoke handler would not have.
  const openEntity = useOpenEntity();

  const addr = facts?.slug ?? (p.slug || DESK_SLUG);

  // The whole panel reloads when the workspace changes, never when a disclosure or the filter does.
  useEffect(() => {
    let live = true;
    setFacts(null); setFailed(null); setCommits(null); setThisPage(false); setShown(HISTORY_PAGE);
    setOpenSha(null); setArmed(null); setSaid(null); setAttaching(false);
    // CLOSED, WITHOUT ASKING ANYTHING (Vexa-ai/vexa#1642). No storage is read here — not the
    // retired `vexa.wsreadme.open:<slug>`, not a versioned successor to it — so a posture written
    // by any earlier build cannot open this panel on arrival, in the founder's browser or anyone's.
    setDetails(false);
    void loadWorkspaceFacts(p.slug)
      .then((f) => { if (live) setFacts(f); })
      .catch((e: unknown) => { if (live) setFailed(presentError(e).headline); });
    return () => { live = false; };
  }, [p.slug]);

  // The history is its own read — the strip needs its COUNT before anything is open, and toggling
  // the page filter or asking for more must not re-read the identity, the tree, the roster and the
  // remote to answer a question about one query parameter.
  useEffect(() => {
    if (!facts) return;
    let live = true;
    setOpenSha(null);
    void loadHistory(addr, thisPage ? p.path : undefined, shown)
      .then((c) => { if (live) setCommits(c); })
      .catch(() => { if (live) setCommits([]); });
    return () => { live = false; };
  }, [facts, addr, thisPage, shown, p.path]);

  const run = useCallback(async (what: () => Promise<string>) => {
    setBusy(true);
    try { setSaid(await what()); setArmed(null); }
    catch (e: unknown) { setSaid(presentError(e).headline); }
    finally { setBusy(false); }
  }, []);

  const openCommit = async (sha: string) => {
    if (openSha === sha) { setOpenSha(null); return; }
    setOpenSha(sha); setDiff(null);
    try {
      const d = await readWorkspaceGitDiff({
        sha, slug: p.slug, path: thisPage ? p.path : undefined,
      });
      setDiff(d.diff || "(nothing changed in this file)");
    } catch (e: unknown) { setDiff(presentError(e).headline); }
  };

  if (failed) {
    return (
      <div data-ws-readme data-ws-state="failed" role="alert" style={{ ...box, borderColor: "var(--danger)" }}>
        <div style={{ ...ty.body, color: "var(--danger)" }}>Could not read this workspace: {failed}</div>
      </div>
    );
  }
  // LOADING IS THE HEADER'S OWN SHAPE, greyed (#1634's design spec, "States"). A sentence saying
  // "Reading the workspace…" is a fourth thing to read on a page whose whole complaint was that it
  // said too much; two bars in the place the two rows will be say the same and then get out of the
  // way without moving the body when they do.
  if (!facts) {
    return (
      <div data-ws-readme data-ws-state="loading" style={box} aria-busy="true"
        aria-label="Reading this workspace">
        <div style={{ ...stripS }}>
          <span style={{ ...barS(96), height: 8, opacity: 0.7 }} />
          <span style={{ ...barS("48%"), height: 15, margin: "3px 0 6px" }} />
          <span style={barS("72%")} />
          <span style={{ ...barS("54%"), marginTop: 6 }} />
        </div>
      </div>
    );
  }

  const owner = facts.owner;
  const remote = facts.remote;
  const sync = (slug?: string) => (slug === undefined ? {} : { slug });
  const syncSlug = p.slug;               // absent = the caller's own desk, which is what the API means
  const more = (commits?.length ?? 0) > shown;
  const listed = commits ? commits.slice(0, shown) : null;

  /** WHAT THE TWO SENTENCES ARE MADE OF. Assembled here and worded in `./workspaceFrontPage`, so
   *  every claim about the words is a pure function a test can hold against fixture data. */
  const fp: FrontPageFacts = {
    kind: facts.kind, name: facts.name, pages: facts.pages, policies: facts.policiesText,
    company: facts.company,
    // THE ADMINISTRATOR IS ASKED FOR BY NAME (Vexa-ai/vexa#1642). This used to be the reader's own
    // first name and only when the reader WAS the administrator, so everybody else — and, when the
    // resolution failed, the administrator himself — read *the admin*. `/api/people/admin` answers
    // it from the company layer's own acceptances, which is where the product already records who
    // writes there; a null answer drops the clause rather than filling it with the role word.
    adminFirstName: facts.kind === "global" ? (facts.admin?.first_name ?? null) : null,
    adminName: facts.kind === "global" ? (facts.admin?.name ?? null) : null,
    myName: facts.me?.name ?? null,
    members: facts.members, mySubject: facts.me?.subject ?? null, myRole: facts.myRole,
    bound: facts.bound,
  };
  const acts = stripActs({ kind: facts.kind, owner, remote });
  const people = peopleClause(fp);
  const pill = kindFact(fp);
  const faces = avatarPeople(fp);
  const changed = facts.change ? lastChangeParts(facts.change) : null;
  // THE TITLE. The README's own first heading, lifted by the pane that renders the body so it is
  // not printed twice (#1634's design spec, point 2); a README with no heading falls back to what
  // this place is called — the company's name on the company layer, the workspace's on a group.
  //
  // …AND NEVER THE EYEBROW AGAIN. The seeded desk README opens `# Your desk`, which is the word the
  // eyebrow already says, and the header rendered it twice in a row — seen in a browser, on the
  // desk. A title that repeats the label above it is not a title.
  const eye = eyebrow(facts.kind);
  const named = (p.title || "").trim()
    || (facts.kind === "global" ? facts.company : facts.kind === "group" ? facts.name : null);
  const heading = named && named.toLowerCase() !== eye.toLowerCase() ? named : null;

  /** OPENING THE DETAILS. History is the door and the three sections are what is behind it — all
   *  three, at once: a person who has just asked for the history should not then have to ask which
   *  of six summaries carries it. Closing it leaves nothing behind, here or in storage. */
  const openDetails = () => { setDetails((was) => !was); };

  /** AN ACT IS A CONVERSATION, NOT A FORM (Vexa-ai/vexa#1632). The press queues a same-target act
   *  on the open chat through `ASK_CHAT_EVENT` — the one door every act on this screen already uses
   *  — and the agent asks for what it needs, confirms in one sentence, and does it.
   *
   *  **Set up policies** is #1627's typed intent, which exists: the server maps the KIND to
   *  `_global/asks/policies-wizard.md` and nothing here composes a sentence. The other three carry
   *  their instruction as text because their intent kinds are not on this branch yet — see the TODO
   *  in `workspaceFrontPage.actInstruction`, which names #1632 and says what replaces it. */
  const fire = (a: StripAct) => {
    if (a.id === "policies") {
      postIntent({ kind: "policies_wizard", workspace: POLICIES_WORKSPACE, path: POLICIES_PATH });
      return;
    }
    // ADDING A MEMBER IS #1632'S OWN ACT, which landed on this branch while this was being built.
    // The strip's button is a second door into it, not a second implementation: the same typed
    // intent, the same ask, the same verb. `Add an editor` is deliberately NOT this — the company
    // layer's editors are a named set in `POLICIES.md`, and `workspace_invite` refuses `_global`
    // precisely because a membership record there would authorise nothing.
    if (a.id === "member") { postIntent({ kind: "member_add", workspace: facts.slug }); return; }
    // WHAT THE ACT CALLS THIS PLACE. A desk is "your desk" and the company layer is the company's
    // own name — never the slug, which is an address: the same rule the chat header keeps (F49,
    // where the founder met `126`) applied to the sentence an act puts in front of the agent.
    const where = {
      workspace: facts.slug,
      name: facts.kind === "desk" ? "your desk"
        : facts.kind === "global" ? (facts.company || "the company layer")
        : facts.name,
    };
    const prompt = actInstruction(a.id, where);
    if (!prompt) return;
    window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
      // The bubble shows the ACT, never the instruction: a bubble that renders the instruction puts
      // words in the person's mouth they did not write (`extend.ts`'s own rule).
      detail: { prompt, display: actDisplay(a, where) },
    }));
  };

  return (
    <div data-ws-readme data-ws-kind={facts.kind} style={box}>
      {/* THE HEADER — eyebrow, title, the people row, the last-change row, then the hairline
          (#1634's design spec, 2026-09-06 22:15Z: *"no one will read it and no one will be happy
          about this, make it a proper design — follow guidelines"*). Capped at 12vh for #1628's
          reason and scrolling rather than clipping for its reason too: a control a person can see
          the top of and cannot click is the one thing this panel refuses to render. */}
      <div data-ws-strip style={stripS}>
        <div data-ws-eyebrow style={eyebrowS}>{eye}</div>
        {heading && <h1 data-ws-title style={titleS}>{heading}</h1>}

        <div data-ws-rows style={rowsS}>
        {/* WHO IS HERE — faces, then the sentence, the count and the one pill as ONE flow, then
            this viewer's acts at the right edge. */}
        <div data-ws-people-row style={{ ...lineS, ...rowFlow }}>
          <Avatars people={faces} />
          <span style={saidS}>
            {people && <span data-ws-line="where">{people}</span>}
            {pageCount(facts.pages) && <>
              {people && <span aria-hidden style={{ color: "var(--t3)" }}>{" · "}</span>}
              <span data-ws-pages>{pageCount(facts.pages)}</span>
            </>}
            {pill && <>{" "}<span data-ws-pill style={pillS}>
              <Icon name={facts.kind === "global" ? "shield" : "cal"} size={11} style={{ opacity: 0.75 }} />
              {pill}
            </span></>}
          </span>
          {/* THE ACTS. Each queues a same-target act on the open chat and none opens a form
              (#1632). History is the disclosure — it opens the sections #1628 built, it is the only
              one a reader gets, and it is the icon alone: it is the act nobody needs a word for,
              and its name is in `aria-label` and the tooltip where a word costs no room. */}
          <span data-ws-acts style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap", flex: "none", marginLeft: "auto" }}>
            {acts.map((a) => {
              const isHistory = a.id === "history";
              return (
                <button key={a.id} data-ws-strip-act={a.id} title={a.why}
                  aria-label={isHistory ? a.label : undefined}
                  {...(isHistory
                    ? { "data-ws-details": "", "aria-expanded": details, "aria-controls": "ws-details" }
                    : {})}
                  onClick={() => (isHistory ? openDetails() : fire(a))}
                  // The open state is a whole `border`, not a `borderColor` beside the shorthand:
                  // React warns on the mix, and closing the disclosure logged it every time.
                  style={{ ...(isHistory ? iconActS : actS), ...(isHistory && details
                    ? { background: surface.raisedHi, border: "1px solid var(--line2)", color: "var(--t1)" } : {}) }}>
                  {isHistory ? <Icon name="history" size={13} /> : a.label}
                </button>
              );
            })}
          </span>
        </div>

        {/* WHAT LAST HAPPENED — a clock, a sentence, and, when exactly one page changed, its TITLE
            as a quiet link that opens it. Two paths and not three: everything with no page to link
            (nothing written yet · several pages · a commit that touched none) is the same sentence
            `lastChangeSentence` composes, so there is one place the words are decided. */}
        <div data-ws-line="changed" style={{ ...lineS, ...rowFlow, gap: 6 }}>
          <Icon name="clock" size={12} style={{ color: "var(--t3)", flex: "none", marginTop: 3 }} />
          <span data-ws-changed style={saidS}>
            {!changed?.page ? lastChangeSentence(facts.change) : <>
              {changed.who ? `${changed.who === "you" ? "You" : changed.who} changed ` : "Changed "}
              <button data-ws-changed-page={changed.page.path} style={quietLink}
                title={`Open ${changed.page.path}`}
                onClick={() => openEntity({ path: changed.page!.path, slug: p.slug })}>
                {changed.thing}
              </button>
              {` ${changed.when}`}
            </>}
          </span>
        </div>
        </div>
      </div>

      {/* …AND THE THREE SECTIONS, when the reader asked for them. Nothing below this line exists in
          the DOM until it does, which is what makes the strip's height rule hold without a scroller
          — and what makes the front page two sentences rather than a panel.

          THERE IS NO SUMMARY LINE HERE (Vexa-ai/vexa#1642). #1628's six summaries were the first
          block under this disclosure, which put *Company layer · 30 pages · … · no repo attached ·
          10+ commits* back in front of the founder one level down. The kind, the page count and the
          last change are said once, in the header; what is left is the people, the repo and the
          history, each under a label that answers nothing. */}
      {details && (<div data-ws-details-region id="ws-details" style={detailsBox}>

      <Section id="people" name="People" first>
          {facts.kind === "desk" && (
            <div data-ws-members="desk" style={{ ...ty.body, color: "var(--t2)", lineHeight: 1.5 }}>
              Its owner writes it. The company&apos;s agents read it for meetings the owner is in.
            </div>
          )}
          {facts.kind === "global" && (
            <div data-ws-members="global" style={{ ...ty.body, color: "var(--t2)", lineHeight: 1.5 }}>
              Everybody here reads it. The administrator writes it, plus any editor they name.
            </div>
          )}
          {facts.kind === "group" && (
            <div data-ws-members="group">
              {facts.members
                ? facts.members.map((m: WorkspaceMember) => (
                    <div key={m.subject} data-ws-member={m.subject} style={{ ...rowS, gap: 6, flexWrap: "wrap" }}>
                      <Icon name="user" size={12} style={{ color: "var(--t3)" }} />
                      <span style={{ ...valS, flex: "0 1 auto" }}>{m.email || m.subject}</span>
                      <span style={{ ...ty.meta, flex: "none" }}>{roleLabel(m.role)}</span>
                      {/* THE ROW'S TWO ACTS ARE NOW QUESTIONS (Vexa-ai/vexa#1632). One press queues
                          the act on this workspace's chat and the agent asks which role, or asks to
                          be sure, in one sentence. The person the act is about is named from the
                          row that was pressed — their address when the roster has one, else the
                          subject — never inferred, because the sentence the agent says back has to
                          be about somebody the reader can see. */}
                      {owner && m.role !== "owner" && (
                        <>
                          <button data-ws-act={`member-role:${m.subject}`} style={btnS}
                            onClick={() => { postIntent({ kind: "member_role", workspace: facts.slug, member: m.email || m.subject }); }}>
                            Change role
                          </button>
                          <button data-ws-act={`member-remove:${m.subject}`} style={dangerS}
                            onClick={() => { postIntent({ kind: "member_remove", workspace: facts.slug, member: m.email || m.subject }); }}>
                            Remove
                          </button>
                        </>
                      )}
                    </div>
                  ))
                : (
                  <div style={{ ...ty.body, color: "var(--t2)", lineHeight: 1.5 }}>
                    You are {facts.myRole ? `a ${roleLabel(facts.myRole)}` : "a member"} here. The full
                    member list is shown to contributors and owners.
                  </div>
                )}
              {/* ADDING A MEMBER IS A CONVERSATION (Vexa-ai/vexa#1632). This used to mint an invite
                  link for a reader and print it here — which is how the founder met
                  `invite role must be one of ('contributor',)` on a control that offered no role at
                  all. It is now one press and no field: *"this add member should just ask chat to do
                  that with mcp, asking their emails etc."* The addresses and the role are what the
                  agent asks for, and there is nothing this page could ask that it would not ask
                  better — the roles need a sentence each, and a select box has nowhere to put one. */}
              {owner && (
                <div style={{ marginTop: 7 }}>
                  <button data-ws-act="member-add" style={btnS}
                    onClick={() => { postIntent({ kind: "member_add", workspace: facts.slug }); }}>
                    Add a member…
                  </button>
                </div>
              )}
            </div>
          )}
      </Section>

      {/* THE REPO. THREE STATES, AND THEY ARE THREE (#1628 point 3): a repo is attached · none is ·
          the read broke. `_global` for the administrator was rendering the second as the third —
          `not readable`, with `Could not read the GitHub state.` in red at the foot — on a workspace
          that simply has no remote. The red line now fires only on a failure and says what failed. */}
      <Section id="repo" name="Repo">
          {facts.remoteFailure && (
            <div data-ws-github-failed role="alert" style={{ ...ty.body, color: "var(--danger)", lineHeight: 1.5 }}>
              Could not read the GitHub state: {facts.remoteFailure}
            </div>
          )}
          {!facts.remoteFailure && remote && !remote.has_home && (
            <div data-ws-github="unattached">
              <div style={{ ...ty.body, color: "var(--t2)", lineHeight: 1.5 }}>No repo attached.</div>
              {/* The affordance is the EXISTING flow, opened where the reader already is — and it is
                  offered only where it can work. The company layer is mounted read-only into every
                  worker and is not one of `attachTargets`' targets, so an "Attach a repo…" button on
                  `_global` would be a control whose only outcome is a refusal — rule 1, again. */}
              {owner && facts.kind !== "global" && (
                <button data-ws-act="attach" style={{ ...btnS, marginTop: 6 }} onClick={() => setAttaching(true)}>
                  Attach a repo…
                </button>
              )}
              {owner && facts.kind === "global" && (
                <div style={{ ...ty.meta, marginTop: 4 }}>
                  The company layer is not attached through this flow — it is mounted read-only into every worker.
                </div>
              )}
            </div>
          )}
          {!facts.remoteFailure && remote?.has_home && (
            <>
              <Fact k="remote" name="Remote">
                <span style={{ ...ty.mono, color: "var(--t2)" }}>{remote.url ?? remote.remote}</span>
              </Fact>
              <Fact k="branch" name="Branch">
                <span style={ty.mono}>{remote.branch ?? "—"}</span>
                <span style={{ ...ty.meta, marginLeft: 8 }}>
                  {remote.tracked ? `${remote.ahead} ahead · ${remote.behind} behind` : "never fetched — ahead/behind unknown"}
                </span>
              </Fact>
              {owner && (
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 7 }}>
                  <button data-ws-act="sync" style={btnS} onClick={() => void run(async () => {
                    const s = await gitRemoteStatus(sync(syncSlug));
                    setFacts({ ...facts, remote: s, remoteFailure: null });
                    return `${s.branch ?? "HEAD"} — ${s.ahead} ahead, ${s.behind} behind.`;
                  })}><Icon name="refresh" size={11} style={{ marginRight: 4 }} />Sync now</button>
                  <Act id="pull" label="Pull" sentence="Fetch and fast-forward from GitHub"
                    busy={busy} armed={armed} onArm={setArmed}
                    onRun={() => void run(async () => {
                      const r = await pullWorkspace(sync(syncSlug));
                      setFacts({ ...facts, remote: await gitRemoteStatus(sync(syncSlug)) });
                      return r.updated ? `Pulled ${r.behind_before} commit(s) onto ${r.branch}.` : `${r.branch} was already up to date.`;
                    })} />
                  <Act id="push" label="Push" sentence={`Push ${remote.branch ?? "HEAD"} to ${remote.remote}`}
                    busy={busy} armed={armed} onArm={setArmed}
                    onRun={() => void run(async () => {
                      const r = await pushWorkspace(sync(syncSlug));
                      setFacts({ ...facts, remote: await gitRemoteStatus(sync(syncSlug)) });
                      return `Pushed ${r.branch} to ${r.remote} (${r.head_sha.slice(0, 8)}).`;
                    })} />
                  <Act id="detach" label="Detach" danger
                    sentence="Stop syncing to GitHub — the files here stay exactly as they are"
                    busy={busy} armed={armed} onArm={setArmed}
                    onRun={() => void run(async () => {
                      const r = await detachWorkspaceRemote(sync(syncSlug));
                      setFacts({ ...facts, remote: await gitRemoteStatus(sync(syncSlug)) });
                      return r.detached ? `Detached from ${r.remote}. Nothing was deleted.` : "There was no GitHub home to detach.";
                    })} />
                </div>
              )}
            </>
          )}
      </Section>

      {/* HISTORY — ten, then *more*, in both scopes (#1628 point 4). */}
      <Section id="history" name="History">
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
            <button data-ws-history-filter aria-pressed={thisPage}
              onClick={() => { setThisPage((v) => !v); setShown(HISTORY_PAGE); }}
              style={{ ...btnS, background: thisPage ? "var(--accentbg)" : surface.raisedHi, color: thisPage ? "var(--accent)" : "var(--t2)", borderColor: thisPage ? "var(--accent)" : "var(--line)" }}>
              This page only
            </button>
            {/* THE LIST SAYS ITS OWN SCOPE. The button is a control, and a control's label is not a
                statement about what is under it: on 2026-09-06 an unpressed *This page only* sat
                above every commit in `_global` and read as a filter that had failed. */}
            <span data-ws-history-scope style={ty.meta}>
              {thisPage ? `only commits touching ${p.path}` : "every commit in this workspace"}
            </span>
          </div>
          <div data-ws-history>
            {listed === null && <div style={ty.meta}>Reading…</div>}
            {listed?.length === 0 && <div style={ty.meta}>No commits here yet.</div>}
            {listed?.map((c) => (
              <CommitRow key={c.sha} c={c} open={openSha === c.sha} diff={diff} onOpen={() => void openCommit(c.sha)} />
            ))}
            {more && (
              <button data-ws-history-more style={{ ...linkBtn, marginTop: 4 }}
                onClick={() => setShown((n) => n + HISTORY_PAGE)}>more</button>
            )}
          </div>
      </Section>

      </div>)}

      {/* THE RECEIPT of whatever was last done here, and everything that could not be read. Both are
          statements of fact and both live at the foot, where a person looks after acting. */}
      {said && <div data-ws-said role="status" style={{ ...ty.meta, marginTop: 8, color: "var(--t2)" }}>{said}</div>}
      {facts.notes.map((n) => (
        <div key={n} data-ws-note style={{ ...ty.meta, marginTop: 5, color: "var(--danger)" }}>{n}</div>
      ))}


      {/* The existing attach dialog, opened from where the question was asked. It is the same
          component the `+` menu opens — one flow, two doors into it, no second implementation. */}
      {attaching && (
        <AttachRepo workspaceId={facts.kind === "desk" ? undefined : facts.slug}
          onClose={() => setAttaching(false)}
          onAttached={() => { setAttaching(false); void loadWorkspaceFacts(p.slug).then(setFacts); }} />
      )}
    </div>
  );
}
