"use client";
/** THE WORKSPACE'S FRONT PAGE — what stands between a workspace README's slug line and its prose.
 *
 *  Founder, 2026-09-06, with the OeNB workspace's `README.md` open in the preview (reached by
 *  clicking the `OeNB` chip in the chat header): *"ok click we want to open the workspace readme —
 *  if it's a workspace readme we want to have data: shared with whom, controls like github sync, git
 *  history lookup, etc."* (Vexa-ai/vexa#1623).
 *
 *  THE PANEL IS NOT PAGE CHROME. It appears on exactly one page per workspace — the README at the
 *  root — and never anywhere else, which is the whole of why it can be this dense: a reader who
 *  opens a workspace's front page is asking about the WORKSPACE, and a reader who opens
 *  `drafts/plan.md` is not. Every other page keeps today's chrome untouched.
 *
 *  FOUR RULES IT IS BUILT ON, each of which the code below can be read against:
 *
 *  1. **A reader sees data and history, no controls.** Not disabled controls, not controls that
 *     explain why they will not work — none. A control whose only outcome is a 403 teaches a person
 *     that the product is broken rather than that they lack the role (`AttachRepo`'s own lesson,
 *     three files over). `facts.owner` is the switch, and the server decides again on every act.
 *  2. **Every control is a confirmed act.** One click arms, a second commits, and the armed state
 *     says what will happen in words. Removing a colleague from a workspace and changing who may
 *     write it are not things a mis-click may do.
 *  3. **Every write is a commit or a receipt.** Membership and role changes commit into the
 *     workspace's own `policy/members.json` server-side; push, pull and detach answer with the
 *     remote and branch they touched. Nothing here writes anything the workspace does not record.
 *  4. **Nothing is guessed.** Every fact is nullable; a read that failed says so in its own section
 *     rather than rendering as a zero. "0 pages" when the tree read failed is a lie about somebody's
 *     own workspace, on the one page whose whole job is to be true about it.
 */
import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { presentError } from "../surfaces/apiClient";
import {
  detachWorkspaceRemote, gitRemoteStatus, mintInvite, pullWorkspace, pushWorkspace,
  readWorkspaceGitDiff, removeWorkspaceMember, setWorkspaceMemberRole,
  type GitCommit, type WorkspaceMember,
} from "../surfaces/workspaceApi";
import { surface, type as ty } from "./tokens";
import {
  DESK_SLUG, kindLabel, loadHistory, loadWorkspaceFacts, roleLabel, type WorkspaceFacts,
} from "./workspaceReadme";

const box: CSSProperties = {
  border: "1px solid var(--line)", borderRadius: 8, background: surface.raised,
  padding: "10px 12px", marginBottom: 16,
};
const sectionS: CSSProperties = { borderTop: "1px solid var(--line)", marginTop: 9, paddingTop: 9 };
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

function Section(p: { label: string; children: ReactNode; first?: boolean }) {
  return (
    <div style={p.first ? undefined : sectionS}>
      <div style={{ ...ty.lens, marginBottom: 6 }}>{p.label}</div>
      {p.children}
    </div>
  );
}

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

export function WorkspaceReadmePanel(p: { slug?: string; path: string }) {
  const [facts, setFacts] = useState<WorkspaceFacts | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [commits, setCommits] = useState<GitCommit[] | null>(null);
  const [thisPage, setThisPage] = useState(false);
  const [openSha, setOpenSha] = useState<string | null>(null);
  const [diff, setDiff] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);   // the receipt of the last act
  const [invite, setInvite] = useState<string | null>(null);

  // The whole panel reloads when the workspace changes, never when the filter does.
  useEffect(() => {
    let live = true;
    setFacts(null); setFailed(null); setCommits(null); setThisPage(false);
    setOpenSha(null); setArmed(null); setSaid(null); setInvite(null);
    void loadWorkspaceFacts(p.slug)
      .then((f) => { if (live) { setFacts(f); setCommits(f.lastChange ? null : []); } })
      .catch((e: unknown) => { if (live) setFailed(presentError(e).headline); });
    return () => { live = false; };
  }, [p.slug]);

  const addr = facts?.slug ?? (p.slug || DESK_SLUG);
  // The history list is its own read: toggling the page filter must not re-read the identity, the
  // tree, the roster and the remote to answer a question about one query parameter.
  useEffect(() => {
    if (!facts) return;
    let live = true;
    setOpenSha(null);
    void loadHistory(addr, thisPage ? p.path : undefined)
      .then((c) => { if (live) setCommits(c); })
      .catch(() => { if (live) setCommits([]); });
    return () => { live = false; };
  }, [facts, addr, thisPage, p.path]);

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

  return (
    <div data-ws-readme data-ws-kind={facts.kind} style={box}>
      {/* 1 — WHAT THIS IS. Kind, name, address, size, and when somebody last touched it. */}
      <Section label="This workspace" first>
        <Fact k="kind" name="Kind">
          {kindLabel(facts.kind)}
          <span style={{ ...ty.mono, marginLeft: 6 }}>{facts.kind}</span>
          {facts.name && <span style={{ ...ty.body, color: "var(--t2)", marginLeft: 6 }}>· {facts.name}</span>}
        </Fact>
        <Fact k="slug" name="Address"><span style={{ ...ty.mono, color: "var(--t2)" }}>{facts.slug}</span></Fact>
        <Fact k="pages" name="Pages">{facts.pages === null ? unknown : `${facts.pages}`}</Fact>
        <Fact k="last" name="Last change">
          {facts.lastChange
            ? <>{facts.lastChange.msg}<span style={{ ...ty.meta, marginLeft: 6 }}>{facts.lastChange.author ?? "unknown"} · {facts.lastChange.when}</span></>
            : <span style={ty.meta}>nothing committed yet</span>}
        </Fact>
        <Fact k="policy" name="Policy">
          {facts.policy ?? <span style={{ ...ty.meta, fontStyle: "italic" }}>no rule stated for this kind</span>}
        </Fact>
      </Section>

      {/* 2 — WHAT DROPPED IN HERE. Only when something did: an empty section on a workspace nothing
          is bound to says nothing and takes a fifth of a narrow panel to say it. */}
      {facts.bound.length > 0 && (
        <Section label="Bound to">
          {facts.bound.map((b) => (
            <div key={b.key} data-ws-bound={b.key} style={rowS}>
              <span style={valS}>{b.title}</span>
              <span style={{ ...ty.meta, flex: "none" }}>
                {b.recurring ? `recurring · ${b.runs} run${b.runs === 1 ? "" : "s"}` : "one meeting"}
              </span>
            </div>
          ))}
        </Section>
      )}

      {/* 3 — SHARED WITH WHOM. The founder's first named ask. */}
      <Section label="Shared with">
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
      </Section>

      {/* 4 — GITHUB SYNC. */}
      <Section label="GitHub">
        {remote === null && <div style={ty.meta}>{unknown}</div>}
        {remote && !remote.has_home && (
          <div style={{ ...ty.body, color: "var(--t2)", lineHeight: 1.5 }}>
            Not attached to a repository.
            {owner && <> <span style={ty.meta}>Use <em>Attach existing repo…</em> in the <code style={ty.mono}>+</code> menu beside the chat&apos;s workspaces.</span></>}
          </div>
        )}
        {remote?.has_home && (
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
                  setFacts({ ...facts, remote: s });
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

      {/* 5 — HISTORY, with a filter for the page this panel is standing on. */}
      <Section label="History">
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
          <button data-ws-history-filter aria-pressed={thisPage} onClick={() => setThisPage((v) => !v)}
            style={{ ...btnS, background: thisPage ? "var(--accentbg)" : surface.raisedHi, color: thisPage ? "var(--accent)" : "var(--t2)", borderColor: thisPage ? "var(--accent)" : "var(--line)" }}>
            This page only
          </button>
          <span style={ty.meta}>{thisPage ? p.path : "the whole workspace"}</span>
        </div>
        <div data-ws-history>
          {commits === null && <div style={ty.meta}>Reading…</div>}
          {commits?.length === 0 && <div style={ty.meta}>No commits here yet.</div>}
          {commits?.map((c) => (
            <div key={c.sha} data-ws-commit={c.sha} style={{ marginBottom: 3 }}>
              <button onClick={() => void openCommit(c.sha)} title={`${c.files?.length ?? 0} file(s)`}
                style={{ ...ty.body, display: "flex", gap: 7, width: "100%", textAlign: "left", alignItems: "baseline",
                         background: openSha === c.sha ? surface.raisedHi : "transparent", border: "none",
                         borderRadius: 6, padding: "3px 5px", cursor: "pointer", color: "var(--t1)" }}>
                <span style={{ ...ty.mono, flex: "none" }}>{c.sha}</span>
                <span style={{ flex: "1 1 0%", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.msg}</span>
                <span style={{ ...ty.meta, flex: "none" }}>{c.author ?? ""} · {c.when}</span>
              </button>
              {openSha === c.sha && (
                <pre data-ws-diff style={{ ...ty.mono, whiteSpace: "pre-wrap", wordBreak: "break-word", margin: "3px 0 8px",
                                           padding: 8, borderRadius: 6, background: "var(--bg)", border: "1px solid var(--line)",
                                           color: "var(--t2)", maxHeight: 260, overflow: "auto" }}>
                  {diff ?? "…"}
                </pre>
              )}
            </div>
          ))}
        </div>
      </Section>

      {/* THE RECEIPT of whatever was last done here, and everything that could not be read. Both are
          statements of fact and both live at the foot, where a person looks after acting. */}
      {said && <div data-ws-said role="status" style={{ ...ty.meta, marginTop: 9, color: "var(--t2)" }}>{said}</div>}
      {facts.notes.map((n) => (
        <div key={n} data-ws-note style={{ ...ty.meta, marginTop: 5, color: "var(--danger)" }}>{n}</div>
      ))}
    </div>
  );
}
