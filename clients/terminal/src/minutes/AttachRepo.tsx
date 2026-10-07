"use client";
/** Import a repository as an independent workspace, preserving its tree and Git identity. */
import { CONNECTIONS_OPEN } from "./connectionEvents";
import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import { workspacePath } from "../app/workspaceRoute";
import { Icon } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import { ApiError, presentError } from "../surfaces/apiClient";
import { redactSecrets } from "../surfaces/redactSecrets";
import { checkRepo } from "../surfaces/repoRef";
import {
  ensureDeployKey, readDeployKey, importWorkspace,
  type DeployKey, type Membership,
} from "../surfaces/workspaceApi";
import { surface, type as ty } from "./tokens";

/** The person's own desk, as a target value. Empty is what both APIs already mean by "no slug". */
export const DESK_TARGET = "";

/** `seed` is the reserved slug that always resolves to the caller's OWN desk, mounted or parked — so the
 *  desk's deploy key can be asked for without first discovering which repo occupies the slot today. */
const DESK_KEY_SLUG = "seed";

export interface AttachTarget { value: string; label: string }

/** Imports always create an independent workspace. */
export function attachTargets(_memberships: Membership[]): AttachTarget[] {
  return [{ value: DESK_TARGET, label: "New workspace" }];
}

/** The sentence the result card says, in the server's own `state` vocabulary. A restore is not a clone
 *  and a no-op is neither; collapsing them would hide the fact a person came here to establish. */
export function attachedSentence(state: string, repo: string, target: string): string {
  if (state === "cloned") return `Cloned ${repo} into ${target}`;
  if (state === "restored") return `Restored ${target} from the copy already here (no re-clone)`;
  return "Already attached — nothing changed";
}

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

export function AttachRepo(p: { workspaceId?: string; embedded?: boolean; onClose: () => void; onAttached?: (target: string) => void }) {
  const targets = attachTargets([]);
  const target = DESK_TARGET;
  const [repo, setRepo] = useState("");
  const [repoIssue, setRepoIssue] = useState<string | null>(null);
  const [ref, setRef] = useState("main");
  const [key, setKey] = useState<DeployKey | null>(null);
  const [keyNote, setKeyNote] = useState<string | null>(null);
  const [keyBusy, setKeyBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [progress, setProgress] = useState("");
  const [busy, setBusy] = useState(false);
  const [importedWorkspace, setImportedWorkspace] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<{ headline: string; verbatim: string } | null>(null);
  const dialog = useRef<HTMLDivElement | null>(null);

  const slug = target || DESK_KEY_SLUG;
  const targetLabel = targets.find((t) => t.value === target)?.label ?? "New workspace";

  // The key state is a hint shown before the person commits to anything, so a read failure is a note
  // rather than a wall — including the ordinary "no key yet" on a workspace nobody has attached.
  useEffect(() => {
    if (!p.embedded) return;
    let live = true;
    setKey(null); setCopied(false); setKeyNote(null);
    void readDeployKey(slug)
      .then((k) => { if (live) setKey(k); })
      .catch((e: unknown) => { if (live) setKeyNote(presentError(e).headline); });
    return () => { live = false; };
  }, [slug, p.embedded]);

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
    setError({ headline: e instanceof ApiError && /git clone failed|no credential for that repository/i.test(e.detail) ? "Repository access failed. Check the repository URL and credential." : presentError(e).headline,
               verbatim: e instanceof ApiError ? redactSecrets(e.detail) : "" });
  }, []);

  const makeKey = async () => {
    if (keyBusy) return;
    setKeyBusy(true); setError(null);
    try {
      setKey(await ensureDeployKey(slug, repo.trim() || undefined)); setKeyNote(null);
      const checked = checkRepo(repo);
      if (checked.ok && checked.url.startsWith("https://github.com/")) {
        setRepo(checked.url.replace("https://github.com/", "git@github.com:"));
        setRepoIssue(null);
      }
    }
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
      return;
    }
    setBusy(true); setError(null); setProgress("Connecting to repository…");
    const url = checked.url;          // the canonical form, not the raw keystrokes
    const branch = ref.trim() || "main";
    try {
      const result = await importWorkspace(url, branch, undefined, setProgress);
      const state = result.cloned ? "cloned" : "already attached";
      setImportedWorkspace(result.workspace);
      setDone(attachedSentence(state, url, result.workspace));
      p.onAttached?.(result.workspace);
    } catch (e: unknown) { fail(e); }
    finally { setBusy(false); }
  };

  return (
    <div data-attach="backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) p.onClose(); }}
      style={p.embedded ? {} : { position: "fixed", inset: 0, zIndex: 60, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,.38)" }}>
      <div ref={dialog} role={p.embedded ? "region" : "dialog"} aria-modal={p.embedded ? undefined : true} aria-label="Load an existing repository"
        style={{ width: p.embedded ? "100%" : 500, boxSizing: "border-box", maxWidth: "92vw", maxHeight: "86vh", overflowY: "auto", padding: 14, background: "var(--sidebar)", border: "1px solid var(--line2)", borderRadius: 10, boxShadow: "0 8px 24px rgba(0,0,0,.35)" }}>

        {busy && <p role="status">{progress}</p>}
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <Icon name="git" size={14} style={{ color: "var(--accent)" }} />
          <span style={{ ...ty.title, flex: 1 }}>Load an existing repository</span>
          <button aria-label="Close" onClick={p.onClose}
            style={{ background: "transparent", border: "none", color: "var(--t3)", cursor: "pointer", display: "flex", padding: 2 }}>
            <Icon name="x" size={14} />
          </button>
        </div>
        <div style={{ ...ty.meta, lineHeight: 1.5, marginBottom: 12 }}>
          Import a repository as its own workspace. Personal and your other workspaces stay unchanged.
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
            <button data-attach="close" onClick={() => { window.location.assign(workspacePath(importedWorkspace)); }} style={{ ...btnS, marginTop: 10 }}>Open workspace</button>
          </div>
        ) : (
          <>
            <div style={{ marginBottom: 10 }}>
              <label htmlFor="attach-target" style={labelS}>Load into</label>
              <select id="attach-target" data-attach="target" value={target} disabled style={fieldS}>
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

            {p.embedded ? <div style={{ ...cardS, marginBottom: 12 }}>
              <div style={labelS}>SSH deploy key (optional)</div>
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

            </div> : <p style={ty.meta}>Uses your saved connection. <button onClick={() => { p.onClose(); window.dispatchEvent(new CustomEvent(CONNECTIONS_OPEN, { detail: { provider: "github" } })); }} style={linkS}>Manage Git connection</button></p>}

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
