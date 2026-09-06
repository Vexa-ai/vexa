"use client";
/** THE WORKSPACE'S FRONT PAGE — ONE HEADER STRIP between a workspace README's slug line and its
 *  prose, and everything else behind a disclosure.
 *
 *  Founder, 2026-09-06, first look at what #1623 built, on `_global/README.md`: *"ups, the workspace
 *  panel should take only the header, 1/8 screen at max, the rest collapsed."* (Vexa-ai/vexa#1628).
 *  What he walked into filled the viewport — five rows of THIS WORKSPACE, then SHARED WITH, then
 *  GITHUB, then twenty commits of HISTORY — and pushed the page's own first line below the fold.
 *
 *  So the panel is now a STRIP: six summaries in one or two lines, each of them the ANSWER rather
 *  than a label for one (`Shared workspace` · `12 pages` · the last change · the roster in five
 *  words · the repo in three · a commit count), and each of them a button that opens exactly that
 *  section under the strip. Nothing is open by default; opening one closes the other, because the
 *  complaint was a wall of sections and two open sections are the beginning of one. The strip is
 *  capped at 12vh — a twelfth-and-a-half of the viewport is what "1/8 screen at max" means, measured
 *  where it can be measured — and no section's content is even RENDERED until its disclosure opens,
 *  so the cap is not a promise the layout has to keep on its own.
 *
 *  WHAT IS REMEMBERED, AND WHERE. Which section a person had open is remembered per workspace, in
 *  their browser and nowhere else: it is a reading posture, not a fact about the workspace, and it
 *  has no business in anybody's git history.
 *
 *  KEYBOARD. The strip is ONE tab stop with six buttons inside it (`role="toolbar"`, roving
 *  tabindex): tab moves past the whole panel, arrows move within it. Six tab stops between the page
 *  title and the page's own text is a tax on every reader who is not looking at the panel.
 *
 *  THE FOUR RULES IT IS STILL BUILT ON, unchanged from #1623 and each readable against the code:
 *
 *  1. **A reader sees data and history, no controls.** Not disabled controls, not controls that
 *     explain why they will not work — none. A control whose only outcome is a 403 teaches a person
 *     that the product is broken rather than that they lack the role (`AttachRepo`'s own lesson,
 *     three files over). `facts.owner` is the switch, and the server decides again on every act.
 *  2. **Every control is a confirmed act.** One click arms, a second commits, and the armed state
 *     says what will happen in words.
 *  3. **Every write is a commit or a receipt.** Membership and role changes commit into the
 *     workspace's own `policy/members.json` server-side; push, pull and detach answer with the
 *     remote and branch they touched.
 *  4. **Nothing is guessed.** Every fact is nullable; a read that failed says so in its own section
 *     rather than rendering as a zero — and, since #1628, *no repo attached* is a STATE and not a
 *     failure: the red line is reserved for a read that actually broke, and it names what broke.
 */
import {
  useCallback, useEffect, useRef, useState,
  type CSSProperties, type KeyboardEvent as ReactKeyboardEvent, type ReactNode,
} from "react";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { presentError } from "../surfaces/apiClient";
import {
  detachWorkspaceRemote, gitRemoteStatus, mintInvite, pullWorkspace, pushWorkspace,
  readWorkspaceGitDiff, removeWorkspaceMember, setWorkspaceMemberRole,
  type GitCommit, type WorkspaceMember,
} from "../surfaces/workspaceApi";
import { AttachRepo } from "./AttachRepo";
import { surface, type as ty } from "./tokens";
import {
  DESK_SLUG, HISTORY_PAGE, kindLabel, lastChangeLine, loadHistory, loadWorkspaceFacts,
  repoInThreeWords, roleLabel, sharedInFiveWords, type WorkspaceFacts,
} from "./workspaceReadme";

const box: CSSProperties = {
  border: "1px solid var(--line)", borderRadius: 8, background: surface.raised,
  padding: "8px 10px", marginBottom: 16,
};
/** THE HEIGHT RULE, as a number the code and its test share. 12% of the viewport, which is inside
 *  the founder's "1/8 screen at max" with the rounding in the reader's favour. */
export const STRIP_MAX_VH = 12;
const stripS: CSSProperties = {
  display: "flex", flexWrap: "wrap", alignItems: "center", gap: "3px 8px",
  maxHeight: `${STRIP_MAX_VH}vh`, overflow: "hidden",
};
const sectionS: CSSProperties = { borderTop: "1px solid var(--line)", marginTop: 8, paddingTop: 8 };
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
  const [focus, setFocus] = useState(0);
  const [openSha, setOpenSha] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);   // the receipt of the last act
  const [invite, setInvite] = useState<string | null>(null);
  const [attaching, setAttaching] = useState(false);
  const strip = useRef<HTMLDivElement | null>(null);

  const addr = facts?.slug ?? (p.slug || DESK_SLUG);

  // The whole panel reloads when the workspace changes, never when a disclosure or the filter does.
  useEffect(() => {
    let live = true;
    setFacts(null); setFailed(null); setCommits(null); setThisPage(false); setShown(HISTORY_PAGE);
    setOpenSha(null); setArmed(null); setSaid(null); setInvite(null); setAttaching(false);
    setOpen(rememberedOpen(p.slug || DESK_SLUG)); setFocus(0);
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

  /** ARROWS MOVE INSIDE THE STRIP, tab moves past it. The focused index is state so the pressed
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
      {/* THE STRIP — six answers, six buttons, one tab stop, 12vh at the very most. */}
      <div ref={strip} data-ws-strip role="toolbar" aria-label="This workspace" aria-orientation="horizontal"
        onKeyDown={onStripKey} style={stripS}>
        {items.map((it, i) => (
          <button key={it.id} data-ws-disclosure={it.id} tabIndex={i === focus ? 0 : -1}
            aria-expanded={open === it.id} aria-controls={`ws-section-${it.id}`}
            title={`${it.name} — ${it.summary}`}
            onClick={() => { setFocus(i); toggle(it.id); }}
            style={summaryS(open === it.id)}>
            <span style={{ ...ty.lens, flex: "none" }}>{it.name}</span>
            <span style={{ color: "var(--t1)", overflow: "hidden", textOverflow: "ellipsis" }}>{it.summary}</span>
          </button>
        ))}
      </div>

      {/* …AND ONE SECTION, if the reader asked for one. Nothing below this line exists in the DOM
          until it does, which is what makes the strip's height rule hold without a scroller. */}
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
                      {owner && m.role !== "owner" && (
                        <>
                          <Act id={`role:${m.subject}`} label={m.role === "viewer" ? "Make contributor" : "Make reader"}
                            sentence={`${m.email || m.subject} → ${m.role === "viewer" ? "contributor" : "reader"}`}
                            busy={busy} armed={armed} onArm={setArmed}
                            onRun={() => void run(async () => {
                              const next = m.role === "viewer" ? "contributor" : "viewer";
                              await setWorkspaceMemberRole(facts.slug, m.subject, next);
                              const f = await loadWorkspaceFacts(p.slug); setFacts(f);
                              return `${m.email || m.subject} is now a ${roleLabel(next)}.`;
                            })} />
                          <Act id={`remove:${m.subject}`} label="Remove" danger
                            sentence={`Remove ${m.email || m.subject} from this workspace`}
                            busy={busy} armed={armed} onArm={setArmed}
                            onRun={() => void run(async () => {
                              await removeWorkspaceMember(facts.slug, m.subject);
                              const f = await loadWorkspaceFacts(p.slug); setFacts(f);
                              return `${m.email || m.subject} was removed.`;
                            })} />
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
              {owner && (
                <div style={{ marginTop: 7 }}>
                  <Act id="invite" label="Add a member…" sentence="Mint an invite link for a reader"
                    busy={busy} armed={armed} onArm={setArmed}
                    onRun={() => void run(async () => {
                      const minted = await mintInvite({ workspace_id: facts.slug, role: "viewer", mode: "open", max_uses: 1 });
                      setInvite(`${window.location.origin}/?invite=${encodeURIComponent(minted.token)}`);
                      return "Invite minted — the link is shown once.";
                    })} />
                </div>
              )}
              {invite && (
                <div data-ws-invite style={{ ...ty.mono, marginTop: 7, wordBreak: "break-all", color: "var(--t1)" }}>
                  {invite}
                  <button style={{ ...linkBtn, marginLeft: 8 }} onClick={() => void copyText(invite)}>copy</button>
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
