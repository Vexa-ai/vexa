"use client";
/** ATTACH AN EXISTING REPO — the one place in minutes mode a person says "we already have our
 *  workspace on GitHub, load it". Three of its choices are load-bearing and none is cosmetic.
 *
 *  THE TARGET IS A CHOICE, NOT AN ASSUMPTION. The same sentence means two different writes: onto the
 *  person's own desk (`swapWorkspace`) or onto a group everyone else reads (`attachSharedWorkspace`).
 *  A viewer of a group may read its workspace and may never replace it — the server refuses that with
 *  403 — so viewer memberships are not offered here at all. A control whose only outcome is a refusal
 *  is worse than no control: it teaches the person that the product is broken rather than that they
 *  lack the role.
 *
 *  THE CREDENTIAL IS NOT A TOKEN BOX. Asking someone to paste a PAT into a web form asks for a secret
 *  that opens every repository they can reach, to solve a problem about ONE repository. So the primary
 *  path is the workspace's own ed25519 deploy key: we show OUR public half, they add it to THEIR repo,
 *  and nothing of theirs ever travels here. The saved PAT stays a fallback for `https://` remotes and
 *  is entered once in the account menu's GitHub token card — never re-asked per repo.
 *
 *  THE REPOSITORY FIELD IS VALIDATED BEFORE IT IS SENT (2026-09-02). Saying "the credential is not a
 *  token box" is not the same as making it impossible to put one in the wrong box, and a founder put a
 *  PAT in THIS field. It was sent, git was told to clone it, and git's answer — `fatal: repository
 *  '<the token>' does not exist` — came back into the card below. So `checkRepo` runs on every
 *  keystroke and on submit: a credential-shaped value never leaves the tab, and the card says which
 *  box it belongs in. The server refuses it too (422, before any git process exists); this is the
 *  first line, and the point of the first line is that the value does not travel.
 *
 *  THE RESULT STATES A STATE. `cloned` · `restored` · `already attached` are three different facts
 *  about where a group's data now is, and "done" is not one of them. On failure the server has already
 *  composed the fix — a 502 whose detail carries the public key and the "say `done` when added"
 *  sentence — so we render that string VERBATIM. Paraphrasing it would drop the key, which is the only
 *  part the person actually needs. */
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { ApiError, presentError } from "../surfaces/apiClient";
import { redactSecrets } from "../surfaces/redactSecrets";
import { checkRepo } from "../surfaces/repoRef";
import {
  attachSharedWorkspace, ensureDeployKey, listSharedMemberships, readDeployKey, swapWorkspace,
  type DeployKey, type Membership, type SwapResult,
} from "../surfaces/workspaceApi";
import { surface, type as ty } from "./tokens";

/** The person's own desk, as a target value. Empty is what both APIs already mean by "no slug". */
export const DESK_TARGET = "";

/** `seed` is the reserved slug that always resolves to the caller's OWN desk, mounted or parked — so the
 *  desk's deploy key can be asked for without first discovering which repo occupies the slot today. */
const DESK_KEY_SLUG = "seed";

export interface AttachTarget { value: string; label: string }

/** Who may be attached ONTO: the desk always, plus every group the person can write to. Viewers are
 *  filtered out here rather than at the server's 403, per the doc-comment above. */
export function attachTargets(memberships: Membership[]): AttachTarget[] {
  return [
    { value: DESK_TARGET, label: "Personal" },
    ...memberships
      .filter((m) => m.role === "owner" || m.role === "contributor")
      .map((m) => ({ value: m.workspace_id, label: m.workspace_id })),
  ];
}

/** The sentence the result card says, in the server's own `state` vocabulary. A restore is not a clone
 *  and a no-op is neither; collapsing them would hide the fact a person came here to establish. */
export function attachedSentence(state: string, repo: string, target: string): string {
  if (state === "cloned") return `Cloned ${repo} into ${target}`;
  if (state === "restored") return `Restored ${target} from the copy already here (no re-clone)`;
  return "Already attached — nothing changed";
}

/** The desk swap answers with flags rather than a `state` word; this is the same three-way fact. */
const deskState = (r: SwapResult): string => (r.cloned ? "cloned" : r.swapped ? "restored" : "already attached");

const fieldS: CSSProperties = {
  ...ty.body, width: "100%", boxSizing: "border-box", padding: "6px 8px", borderRadius: 6,
  border: "1px solid var(--line)", background: "var(--panel2)", color: "var(--t1)",
};
const btnS: CSSProperties = {
  ...ty.control, display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 6,
  border: "1px solid var(--line)", background: surface.raised, color: "var(--t1)", cursor: "pointer",
};
const linkS: CSSProperties = {
  ...ty.meta, background: "transparent", border: "none", padding: 0, color: "var(--t2)",
  cursor: "pointer", textDecoration: "underline", textUnderlineOffset: 2,
};
const labelS: CSSProperties = { ...ty.lens, display: "block", marginBottom: 5 };
const cardS: CSSProperties = { border: "1px solid var(--line)", borderRadius: 8, padding: 10, background: "var(--panel2)" };

export function AttachRepo(p: { workspaceId?: string; onClose: () => void; onAttached?: (target: string) => void }) {
  const [targets, setTargets] = useState<AttachTarget[]>([{ value: DESK_TARGET, label: "Personal" }]);
  const [target, setTarget] = useState<string>(p.workspaceId ?? DESK_TARGET);
  const [repo, setRepo] = useState("");
  const [repoIssue, setRepoIssue] = useState<string | null>(null);
  const [ref, setRef] = useState("main");
  const [key, setKey] = useState<DeployKey | null>(null);
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [token, setToken] = useState("");
  const [tokenOpen, setTokenOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<{ headline: string; verbatim: string } | null>(null);
  const dialog = useRef<HTMLDivElement | null>(null);

  const slug = target || DESK_KEY_SLUG;
  const targetLabel = targets.find((t) => t.value === target)?.label ?? "Personal";

  useEffect(() => {
    let live = true;
    void listSharedMemberships()
      .then((ms) => { if (live) setTargets(attachTargets(ms)); })
      // The membership list only ADDS options; failing it must not block the desk lane, which is the
      // one that always exists. The attach call itself is loud, so nothing is silently swallowed.
      .catch((e: unknown) => { if (live) setKeyNote(presentError(e).headline); });
    return () => { live = false; };
  }, []);

  // The key state is a hint shown before the person commits to anything, so a read failure is a note
  // rather than a wall — including the ordinary "no key yet" on a workspace nobody has attached.
  useEffect(() => {
    let live = true;
    setKey(null); setCopied(false); setKeyNote(null);
    void readDeployKey(slug)
      .then((k) => { if (live) setKey(k); })
      .catch((e: unknown) => { if (live) setKeyNote(presentError(e).headline); });
    return () => { live = false; };
  }, [slug]);

  // A dialog that outlives its own dismissal is worse than no dialog — Escape closes, as does the
  // backdrop. (Click-away is handled on the backdrop itself rather than a document listener, so a
  // click inside the dialog can never be mistaken for a click outside it.)
  useEffect(() => {
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") p.onClose(); };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [p.onClose]);

  const fail = useCallback((e: unknown) => {
    // `verbatim` exists to carry the deploy-key answer through unparaphrased; that same channel is
    // how a credential would reach the screen, so it is scrubbed. The public key survives — it is not
    // a secret and the scrubber leaves git object ids and key material alone (see redactSecrets).
    setError({ headline: presentError(e).headline,
               verbatim: e instanceof ApiError ? redactSecrets(e.detail) : "" });
  }, []);

  const makeKey = async () => {
    if (keyBusy) return;
    setKeyBusy(true); setError(null);
    try { setKey(await ensureDeployKey(slug, repo.trim() || undefined)); setKeyNote(null); }
    catch (e: unknown) { fail(e); }
    finally { setKeyBusy(false); }
  };

  const attach = async () => {
    if (busy || !repo.trim()) return;
    // The value is checked HERE as well as on every keystroke, because a paste that never fires a
    // change handler, an autofill, or a stale `repoIssue` must not be the thing standing between a
    // credential and the wire.
    const checked = checkRepo(repo);
    if (!checked.ok) {
      setRepoIssue(checked.sentence);
      setToken("");
      return;
    }
    // P15: a one-off token is read once, handed to the call, and gone from this component before the
    // request is even in flight — it is never re-rendered, never re-sent and never logged.
    const oneOff = token.trim() || undefined;
    setToken(""); setTokenOpen(false);
    setBusy(true); setError(null);
    const url = checked.url;          // the canonical form, not the raw keystrokes
    const branch = ref.trim() || "main";
    try {
      const state = target
        ? (await attachSharedWorkspace(target, { repo: url, ref: branch, token: oneOff })).state
        : deskState(await swapWorkspace(url, branch, oneOff));
      setDone(attachedSentence(state, url, targetLabel));
      p.onAttached?.(target);
    } catch (e: unknown) { fail(e); }
    finally { setBusy(false); }
  };

  return (
    <div data-attach="backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) p.onClose(); }}
      style={{ position: "fixed", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.38)" }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-label="Load an existing repository"
        style={{ width: 500, maxWidth: "92vw", maxHeight: "86vh", overflowY: "auto", padding: 14, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 10, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>

        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <Icon name="git" size={14} style={{ color: "var(--accent)" }} />
          <span style={{ ...ty.title, flex: 1 }}>Load an existing repository</span>
          <button aria-label="Close" onClick={p.onClose}
            style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 2 }}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div style={{ ...ty.meta, lineHeight: 1.5, marginBottom: 12 }}>
          Point a workspace at a repository you have. The tree that is there now is parked, not
          destroyed.
        </div>

        {error && (
          <div role="alert" data-attach="error" style={{ ...cardS, marginBottom: 12, borderColor: "var(--danger)" }}>
            <div style={{ ...ty.body, color: "var(--danger)" }}>⚠ {error.headline}</div>
            {error.verbatim && (
              <pre data-attach="detail"
                style={{ ...ty.mono, whiteSpace: "pre-wrap", wordBreak: "break-all", userSelect: "text", margin: "8px 0 0", color: "var(--t2)", lineHeight: 1.5 }}>{error.verbatim}</pre>
            )}
          </div>
        )}

        {done ? (
          <div data-attach="result" style={cardS}>
            <div style={{ ...ty.bodyStrong, color: "var(--t1)", display: "flex", alignItems: "center", gap: 7 }}>
              <Icon name="check" size={14} style={{ color: "var(--accent)" }} />{done}
            </div>
            <button data-attach="close" onClick={p.onClose} style={{ ...btnS, marginTop: 10 }}>Done</button>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 10 }}>
              <label htmlFor="attach-target" style={labelS}>Load into</label>
              <select id="attach-target" data-attach="target" value={target} onChange={(e) => setTarget(e.target.value)} style={fieldS}>
                {targets.map((t) => <option key={t.value || "personal"} value={t.value}>{t.label}</option>)}
              </select>
            </div>

            <div style={{ marginBottom: 10 }}>
              <label htmlFor="attach-repo" style={labelS}>Repository</label>
              <input id="attach-repo" data-attach="repo" autoFocus value={repo} disabled={busy}
                aria-invalid={repoIssue ? true : undefined}
                aria-describedby={repoIssue ? "attach-repo-issue" : undefined}
                onChange={(e) => {
                  const v = e.target.value;
                  setRepo(v);
                  // A token is called out the moment it appears, before there is anything to submit.
                  // Everything else is only judged once the person has typed enough to be judged —
                  // shouting "not a repository" at `g` is noise that teaches people to ignore it.
                  const c = checkRepo(v);
                  setRepoIssue(!v.trim() ? null : c.ok ? null : c.kind === "token" ? c.sentence : null);
                }}
                placeholder="git@github.com:acme/kg.git" style={{ ...fieldS, borderColor: repoIssue ? "var(--danger)" : "var(--line)" }} />
              {repoIssue && (
                <div id="attach-repo-issue" data-attach="repo-issue" role="alert"
                  style={{ ...ty.meta, color: "var(--danger)", marginTop: 5, lineHeight: 1.45 }}>{repoIssue}</div>
              )}
            </div>

            <div style={{ marginBottom: 12 }}>
              <label htmlFor="attach-ref" style={labelS}>Branch</label>
              <input id="attach-ref" data-attach="ref" value={ref} disabled={busy}
                onChange={(e) => setRef(e.target.value)} style={fieldS} />
            </div>

            <div style={{ ...cardS, marginBottom: 12 }}>
              <div style={labelS}>Credential</div>
              <div style={{ ...ty.meta, lineHeight: 1.5, marginBottom: 8 }}>
                {key?.public_key
                  ? `${targetLabel} has a deploy key${key.fingerprint ? ` — ${key.fingerprint}` : ""}. It has to be on the repository before an ssh remote will answer.`
                  : "Nothing to paste. We generate a key for this workspace; you add our public half to your repository, and the private half never leaves this server."}
              </div>
              {keyNote && <div style={{ ...ty.meta, color: "var(--danger)", marginBottom: 8 }}>{keyNote}</div>}
              <button data-attach="usekey" onClick={() => void makeKey()} disabled={keyBusy} style={{ ...btnS, opacity: keyBusy ? 0.6 : 1 }}>
                <Icon name="key" size={13} />{keyBusy ? "Generating…" : "Use this deploy key"}
              </button>

              {key?.public_key && (
                <div data-attach="pubkey-block" style={{ marginTop: 10 }}>
                  <code data-attach="pubkey" tabIndex={0}
                    style={{ ...ty.mono, display: "block", userSelect: "all", wordBreak: "break-all", color: "var(--t1)", background: "var(--bg)", border: "1px solid var(--line)", borderRadius: 6, padding: 8, lineHeight: 1.5 }}>{key.public_key}</code>
                  <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, flexWrap: "wrap" }}>
                    <button data-attach="copykey" onClick={() => { void copyText(key.public_key ?? ""); setCopied(true); }} style={btnS}>
                      <Icon name="copy" size={12} />{copied ? "copied" : "Copy"}
                    </button>
                    {/* Only when the server gave us one — a settings URL we guessed would 404 on the
                        person, which is worse than making them find the page themselves. */}
                    {key.add_at && (
                      <a data-attach="addat" href={key.add_at} target="_blank" rel="noreferrer" style={{ ...ty.meta, color: "var(--accent)" }}>
                        Add it on GitHub →
                      </a>
                    )}
                    <span style={{ ...ty.meta }}>Add as {key.add_as}.</span>
                  </div>
                  {key.then && <div style={{ ...ty.meta, marginTop: 6 }}>Then {key.then}.</div>}
                </div>
              )}

              <div style={{ borderTop: "1px solid var(--line)", marginTop: 12, paddingTop: 10 }}>
                <button data-attach="token-toggle" onClick={() => setTokenOpen((v) => !v)} style={linkS}>
                  Use a saved token instead
                </button>
                {tokenOpen && (
                  <div style={{ marginTop: 8 }}>
                    <div style={{ ...ty.meta, lineHeight: 1.5, marginBottom: 8 }}>
                      An <code style={ty.mono}>https://</code> remote can use the token saved under your
                      account menu → GitHub token — entered once, reused for every repo. Or paste a
                      one-off below: it travels with this one call and is kept nowhere.
                    </div>
                    <input data-attach="token" type="password" value={token} disabled={busy}
                      onChange={(e) => setToken(e.target.value)} placeholder="ghp_… (one-off, not saved)" style={fieldS} />
                  </div>
                )}
              </div>
            </div>

            <button data-attach="submit" onClick={() => void attach()} disabled={busy || !repo.trim() || !!repoIssue}
              style={{ ...btnS, background: "var(--accent)", color: "var(--on-accent)", border: "none", opacity: busy || !repo.trim() || repoIssue ? 0.5 : 1 }}>
              {busy ? "Loading…" : "Attach"}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
