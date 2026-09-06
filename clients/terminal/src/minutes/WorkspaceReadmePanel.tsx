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
 *  no path. All of it is still true and still one click away — the six summaries and the sections
 *  #1628 built are behind **History**, the one disclosure at the end of line two, unchanged. The
 *  12vh cap stays on the strip for the reason #1628 put it there.
 *
 *  WHAT IS REMEMBERED, AND WHERE. Which section a person had open is remembered per workspace, in
 *  their browser and nowhere else: it is a reading posture, not a fact about the workspace, and it
 *  has no business in anybody's git history. A first visit opens nothing; a return opens what you
 *  left open, which is the same rule seen from the other side.
 *
 *  KEYBOARD. The section summaries are ONE tab stop with six buttons inside them (`role="toolbar"`,
 *  roving tabindex): tab moves past them, arrows move within. The strip's own acts are ordinary
 *  buttons — there are at most three and each is a different thing to do.
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
  useCallback, useEffect, useRef, useState,
  type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode,
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
import { surface, type as ty } from "./tokens";
import {
  actDisplay, actInstruction, lineOne, lineTwo, stripActs,
  type StripAct, type FrontPageFacts,
} from "./workspaceFrontPage";
import {
  DESK_SLUG, HISTORY_PAGE, kindLabel, lastChangeLine, loadHistory, loadWorkspaceFacts,
  repoInThreeWords, roleLabel, sharedInFiveWords, type WorkspaceFacts,
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
  display: "flex", flexWrap: "wrap", alignItems: "center", gap: "2px 2px",
  // The cap is the founder's; `auto` rather than `hidden` is this file's own rule 1 applied to the
  // cap itself. A 384px panel can wrap six summaries past 12vh, and clipping them would leave a
  // disclosure a person can see the top of and cannot click — a control that is present and does
  // not work, which is the thing this panel refuses to render.
  maxHeight: `${STRIP_MAX_VH}vh`, overflowY: "auto", overflowX: "hidden",
};
const sectionS: CSSProperties = { borderTop: "1px solid var(--line)", marginTop: 8, paddingTop: 8 };
/** A sentence, not a row of chips: the size and colour of the line under a shared document's
 *  title, and it WRAPS — a sentence that ellipsizes is a sentence with its ending taken away. */
const lineS: CSSProperties = { ...ty.meta, color: "var(--t2)", lineHeight: 1.55, minWidth: 0 };
/** An act. Quiet enough to sit inside a grey line, and unmistakably a button. */
const actS: CSSProperties = {
  ...ty.meta, flex: "none", padding: "1px 8px", borderRadius: 999, cursor: "pointer",
  border: "1px solid var(--line)", background: "transparent", color: "var(--t2)", lineHeight: 1.6,
};
const rowS: CSSProperties = { display: "flex", alignItems: "baseline", gap: 8, minWidth: 0, lineHeight: 1.5 };
const keyS: CSSProperties = { ...ty.meta, flex: "none", width: 74 };
const valS: CSSProperties = { ...ty.body, color: "var(--t1)", flex: "1 1 0%", minWidth: 0, wordBreak: "break-word" };
const btnS: CSSProperties = {
  ...ty.chip, flex: "none", padding: "2px 8px", borderRadius: 6, cursor: "pointer",
  border: "1px solid var(--line)", background: surface.raisedHi, color: "var(--t2)",
};
const dangerS: CSSProperties = { ...btnS, borderColor: "var(--danger)", color: "var(--danger)" };
const linkBtn: CSSProperties = {
  ...ty.meta, background: "transparent", border: "none", padding: 0, color: "var(--accent)",
  cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2,
};
const summaryS = (open: boolean): CSSProperties => ({
  ...ty.meta, display: "inline-flex", alignItems: "baseline", gap: 5, maxWidth: "100%",
  padding: "2px 6px", borderRadius: 6, cursor: "pointer", border: "1px solid transparent",
  background: open ? surface.raisedHi : "transparent",
  borderColor: open ? "var(--line)" : "transparent",
  color: "var(--t2)", overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis",
});

function Fact(p: { k: string; name: string; children: ReactNode }) {
  return (
    <div style={rowS} data-ws-fact={p.k}>
      <span style={keyS}>{p.name}</span>
      <span style={valS}>{p.children}</span>
    </div>
  );
}

const unknown = <span style={{ ...ty.meta, fontStyle: "italic" }}>not readable</span>;

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

/** The six things the strip says, in the order it says them. Ids are stable because they are what
 *  the remembered open-state is written as. */
type SectionId = "this" | "pages" | "last" | "shared" | "github" | "history";

const OPEN_KEY = (slug: string) => `vexa.wsreadme.open:${slug}`;
/** The reader's posture, in their own browser. Every access is guarded: a private window with site
 *  data blocked throws on the getter itself, and a panel that cannot render because storage is off
 *  would be a worse failure than forgetting which section was open. */
const rememberedOpen = (slug: string): SectionId | null => {
  try { return (window.localStorage.getItem(OPEN_KEY(slug)) as SectionId | null) || null; }
  catch { return null; }
};
const remember = (slug: string, id: SectionId | null) => {
  try { if (id) window.localStorage.setItem(OPEN_KEY(slug), id); else window.localStorage.removeItem(OPEN_KEY(slug)); }
  catch { /* storage off — the posture is simply not remembered */ }
};

export function WorkspaceReadmePanel(p: { slug?: string; path: string }) {
  const [facts, setFacts] = useState<WorkspaceFacts | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [commits, setCommits] = useState<GitCommit[] | null>(null);
  const [shown, setShown] = useState(HISTORY_PAGE);
  const [thisPage, setThisPage] = useState(false);
  const [open, setOpen] = useState<SectionId | null>(null);
  // THE ONE DISCLOSURE (#1634 rule 6). Everything #1628 built lives under it; History is the button
  // that opens it. It starts open only when this reader left a section open here — a first visit
  // shows two sentences and nothing else.
  const [details, setDetails] = useState(false);
  const [focus, setFocus] = useState(0);
  const [openSha, setOpenSha] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);   // the receipt of the last act
  const [attaching, setAttaching] = useState(false);
  const strip = useRef<HTMLDivElement | null>(null);

  const addr = facts?.slug ?? (p.slug || DESK_SLUG);

  // The whole panel reloads when the workspace changes, never when a disclosure or the filter does.
  useEffect(() => {
    let live = true;
    setFacts(null); setFailed(null); setCommits(null); setThisPage(false); setShown(HISTORY_PAGE);
    setOpenSha(null); setArmed(null); setSaid(null); setAttaching(false);
    const posture = rememberedOpen(p.slug || DESK_SLUG);
    setOpen(posture); setDetails(posture !== null); setFocus(0);
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

  const toggle = useCallback((id: SectionId) => {
    setOpen((was) => {
      const next = was === id ? null : id;
      remember(p.slug || DESK_SLUG, next);
      return next;
    });
  }, [p.slug]);

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
  if (!facts) {
    return <div data-ws-readme data-ws-state="loading" style={{ ...box, ...ty.meta }}>Reading the workspace…</div>;
  }

  const owner = facts.owner;
  const remote = facts.remote;
  const sync = (slug?: string) => (slug === undefined ? {} : { slug });
  const syncSlug = p.slug;               // absent = the caller's own desk, which is what the API means
  const last = facts.lastChange;
  const more = (commits?.length ?? 0) > shown;
  const listed = commits ? commits.slice(0, shown) : null;
  const historyCount = commits === null ? "reading…"
    : `${more ? `${shown}+` : commits.length} commit${!more && commits.length === 1 ? "" : "s"}`;

  /** The strip, as data — so the roving tabindex and the sections cannot disagree about the order. */
  const items: { id: SectionId; name: string; summary: string }[] = [
    { id: "this", name: "Kind", summary: kindLabel(facts.kind) },
    { id: "pages", name: "Pages", summary: facts.pages === null ? "not readable" : `${facts.pages} page${facts.pages === 1 ? "" : "s"}` },
    { id: "last", name: "Last change", summary: lastChangeLine(facts.lastChange) },
    { id: "shared", name: "Shared with", summary: sharedInFiveWords(facts) },
    { id: "github", name: "GitHub", summary: repoInThreeWords(remote, facts.remoteFailure) },
    { id: "history", name: "History", summary: historyCount },
  ];

  /** WHAT THE TWO SENTENCES ARE MADE OF. Assembled here and worded in `./workspaceFrontPage`, so
   *  every claim about the words is a pure function a test can hold against fixture data. */
  const fp: FrontPageFacts = {
    kind: facts.kind, name: facts.name, pages: facts.pages, policies: facts.policiesText,
    company: facts.company,
    // THE ADMINISTRATOR'S FIRST NAME, and only when this reader IS the administrator. `me` is who
    // is READING, not who writes here — so on `_global` it names the writer only where the two are
    // the same person, and everybody else reads "the admin". A reader's own name on a sentence
    // about somebody else's permission would be a confident lie in the first line a person meets.
    adminFirstName: facts.kind === "global" && owner ? (facts.me?.first_name ?? null) : null,
    members: facts.members, mySubject: facts.me?.subject ?? null, myRole: facts.myRole,
    bound: facts.bound,
  };
  const acts = stripActs({ kind: facts.kind, owner, remote });

  /** OPENING THE DETAILS. History is the door, so History is what it opens — and closing it leaves
   *  the section it opened remembered, which is what brings a reader back where they were. */
  const openDetails = () => {
    setDetails((was) => {
      if (!was && open === null) { setOpen("history"); remember(p.slug || DESK_SLUG, "history"); }
      return !was;
    });
  };

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

  /** ARROWS MOVE INSIDE THE SECTION SUMMARIES, tab moves past them. The focused index is state so the pressed
   *  button keeps `tabIndex=0` after it is clicked — a toolbar that resets its entry point every
   *  time you use it sends the next Tab somewhere the reader did not leave it. */
  const onStripKey = (e: ReactKeyboardEvent) => {
    const at = ["ArrowRight", "ArrowDown"].includes(e.key) ? focus + 1
      : ["ArrowLeft", "ArrowUp"].includes(e.key) ? focus - 1
      : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : null;
    if (at === null) return;
    e.preventDefault();
    const next = (at + items.length) % items.length;
    setFocus(next);
    strip.current?.querySelectorAll<HTMLButtonElement>("[data-ws-disclosure]")[next]?.focus();
  };

  return (
    <div data-ws-readme data-ws-kind={facts.kind} style={box}>
      {/* TWO SENTENCES — where you are and who is here, then what last happened and what you may
          do about it. Capped at 12vh for #1628's reason and scrolling rather than clipping for its
          reason too: a control a person can see the top of and cannot click is the one thing this
          panel refuses to render. */}
      <div data-ws-strip style={stripS}>
        <div data-ws-line="where" style={lineS}>{lineOne(fp)}</div>
        <div data-ws-line="changed" style={{ ...lineS, display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
          <span data-ws-changed style={{ flex: "0 1 auto", minWidth: 0 }}>{lineTwo(fp, facts.change)}</span>
          {/* THE ACTS. Each queues a same-target act on the open chat and none opens a form
              (#1632). History is the disclosure — it opens the sections #1628 built, and it is the
              only one a reader gets. */}
          <span data-ws-acts style={{ display: "inline-flex", alignItems: "center", gap: 5, flexWrap: "wrap" }}>
            {acts.map((a) => (
              <button key={a.id} data-ws-strip-act={a.id} title={a.why}
                {...(a.id === "history"
                  ? { "data-ws-details": "", "aria-expanded": details, "aria-controls": "ws-details" }
                  : {})}
                onClick={() => (a.id === "history" ? openDetails() : fire(a))}
                style={{ ...actS, ...(a.id === "history" && details
                  ? { background: surface.raisedHi, borderColor: "var(--line2)", color: "var(--t1)" } : {}) }}>
                {a.label}
              </button>
            ))}
          </span>
        </div>
      </div>

      {/* …AND EVERYTHING #1628 BUILT, when the reader asked for it. Nothing below this line exists
          in the DOM until it does, which is what makes the strip's height rule hold without a
          scroller — and what makes the front page two sentences rather than a panel. */}
      {details && (<div data-ws-details-region id="ws-details" style={detailsBox}>
      <div ref={strip} data-ws-sections role="toolbar" aria-label="This workspace" aria-orientation="horizontal"
        onKeyDown={onStripKey} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "2px 2px" }}>
        {/* THE SUMMARY IS THE WHOLE BUTTON. Its category name ("Kind", "GitHub") is carried by the
            accessible name and the tooltip and NOT printed beside it: on the 384px panel this lives
            in, six printed labels doubled the width and cost the row its two lines — and `Shared
            workspace`, `12 pages`, `main, 2 ahead` say what they are without being told. */}
        {items.map((it, i) => (
          <span key={it.id} style={{ display: "inline-flex", alignItems: "center", minWidth: 0 }}>
            {i > 0 && <span aria-hidden style={{ ...ty.meta, flex: "none", opacity: 0.5 }}>·</span>}
            <button data-ws-disclosure={it.id} tabIndex={i === focus ? 0 : -1}
              aria-expanded={open === it.id} aria-controls={`ws-section-${it.id}`}
              aria-label={`${it.name}: ${it.summary}`} title={`${it.name} — ${it.summary}`}
              onClick={() => { setFocus(i); toggle(it.id); }}
              style={summaryS(open === it.id)}>
              {it.summary}
            </button>
          </span>
        ))}
      </div>

      {open === "this" && (
        <div data-ws-section="this" id="ws-section-this" style={sectionS}>
          <Fact k="kind" name="Kind">
            {kindLabel(facts.kind)}
            <span style={{ ...ty.mono, marginLeft: 6 }}>{facts.kind}</span>
            {facts.name && <span style={{ ...ty.body, color: "var(--t2)", marginLeft: 6 }}>· {facts.name}</span>}
          </Fact>
          <Fact k="slug" name="Address"><span style={{ ...ty.mono, color: "var(--t2)" }}>{facts.slug}</span></Fact>
          <Fact k="policy" name="Policy">
            {facts.policy ?? <span style={{ ...ty.meta, fontStyle: "italic" }}>no rule stated for this kind</span>}
          </Fact>
          {/* WHAT DROPPED IN HERE. Only when something did: an empty list on a workspace nothing is
              bound to says nothing and takes a line to say it. */}
          {facts.bound.map((b) => (
            <div key={b.key} data-ws-bound={b.key} style={rowS}>
              <span style={keyS}>Bound to</span>
              <span style={valS}>{b.title}</span>
              <span style={{ ...ty.meta, flex: "none" }}>
                {b.recurring ? `recurring · ${b.runs} run${b.runs === 1 ? "" : "s"}` : "one meeting"}
              </span>
            </div>
          ))}
        </div>
      )}

      {open === "pages" && (
        <div data-ws-section="pages" id="ws-section-pages" style={sectionS}>
          {facts.pageList === null && <div style={ty.meta}>{unknown}</div>}
          {facts.pageList?.length === 0 && <div style={ty.meta}>No pages here yet.</div>}
          {facts.pageList && facts.pageList.length > 0 && (
            <div style={{ maxHeight: 220, overflowY: "auto" }}>
              {facts.pageList.map((f) => (
                <div key={f} data-ws-page={f} style={{ ...ty.mono, color: "var(--t2)", lineHeight: 1.6 }}>{f}</div>
              ))}
            </div>
          )}
        </div>
      )}

      {open === "last" && (
        <div data-ws-section="last" id="ws-section-last" style={sectionS}>
          {last
            ? <CommitRow c={last} open={openSha === last.sha} diff={diff} onOpen={() => void openCommit(last.sha)} />
            : <div style={ty.meta}>Nothing has been committed here yet.</div>}
        </div>
      )}

      {open === "shared" && (
        <div data-ws-section="shared" id="ws-section-shared" style={sectionS}>
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
        </div>
      )}

      {/* GITHUB. THREE STATES, AND THEY ARE THREE (#1628 point 3): a repo is attached · none is ·
          the read broke. `_global` for the administrator was rendering the second as the third —
          `not readable`, with `Could not read the GitHub state.` in red at the foot — on a workspace
          that simply has no remote. The red line now fires only on a failure and says what failed. */}
      {open === "github" && (
        <div data-ws-section="github" id="ws-section-github" style={sectionS}>
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
        </div>
      )}

      {/* HISTORY — ten, then *more*, in both scopes (#1628 point 4). */}
      {open === "history" && (
        <div data-ws-section="history" id="ws-section-history" style={sectionS}>
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
        </div>
      )}

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
