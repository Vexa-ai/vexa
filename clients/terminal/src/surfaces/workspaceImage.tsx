"use client";
/** A workspace's image — "Change image…" and the owner's one-click answer to a proposal (UI decision
 *  15f). The image lives in the workspace (`.vexa/icon.*`); agent-api stores, sanitises and serves it
 *  (`/api/workspaces/{slug}/identity/*`). The agent sets it through `workspace_icon_set`; this is
 *  the person's own door: pick a standard icon (founder 2026-10-18: "pick icons from a large selection
 *  of standard icons at creation unless changed"), upload a file, use the company's logo, or reset. */
import { useEffect, useRef, useState } from "react";
import { Button, Dialog } from "../ui-kit";
import { Identity } from "../ui-kit/Identity";
import { WORKSPACE_IMAGE_CHANGED } from "../platform/identity";
import { GlyphPicker } from "../ui-kit/WorkspaceGlyph";

export type ImageState = {
  source: "workspace" | "company" | "glyph" | null;
  /** the standard icon it shows (or would, under an image), and who chose it */
  glyph?: string | null;
  glyph_by?: "auto" | "model" | "person" | "derived" | null;
  domain: string | null;
  owner: boolean;
  shared: boolean;
  proposal: { by: string; at: number; source: string; kind: string } | null;
};
export type ImageSet = { svg?: string; data_b64?: string; type?: string; use?: "company_logo"; reset?: boolean; site?: string;
  /** a standard icon by name, or "auto" to pick one from the name and purpose */
  glyph?: string; propose?: boolean };

const base = (slug: string) => `/api/workspaces/${encodeURIComponent(slug)}/identity`;
export { WORKSPACE_IMAGE_CHANGED };

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof d?.detail === "string" ? d.detail : `The image was not changed (${r.status}).`);
  return d as T;
}

export const readImageState = (slug: string) => json<ImageState>(`${base(slug)}/state`);

export async function setWorkspaceImage(slug: string, body: ImageSet): Promise<{ set?: boolean; proposed?: boolean }> {
  const out = await json<{ set?: boolean; proposed?: boolean }>(`${base(slug)}/icon`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(WORKSPACE_IMAGE_CHANGED, { detail: slug }));
  return out;
}

export async function answerImageProposal(slug: string, accept: boolean): Promise<void> {
  await json(`${base(slug)}/proposal`, { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accept }) });
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(WORKSPACE_IMAGE_CHANGED, { detail: slug }));
}

const KIND_OF: Record<string, string> = { "image/svg+xml": "svg", "image/png": "png", "image/webp": "webp" };

async function fileBody(f: File): Promise<ImageSet> {
  const type = KIND_OF[f.type] ?? f.name.split(".").pop()?.toLowerCase() ?? "";
  if (type === "svg") return { svg: await f.text() };
  const buf = new Uint8Array(await f.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
  return { data_b64: btoa(bin), type };
}

export function WorkspaceImageDialog({ slug, name, open, onClose }: { slug: string; name: string; open: boolean; onClose: () => void }) {
  const [st, setSt] = useState<ImageState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setErr(""); setNote(""); readImageState(slug).then(setSt).catch(() => setSt(null)); } }, [open, slug]);
  const run = async (body: ImageSet | Promise<ImageSet>) => {
    setBusy(true); setErr("");
    try {
      const out = await setWorkspaceImage(slug, await body);
      if (out.proposed) { setNote("Proposed — the owner accepts it."); return; }
      onClose();
    } catch (e) { setErr(e instanceof Error ? e.message : "The image was not changed."); }
    finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onClose={onClose} title="Change icon" width="wide" footer={<Button onClick={onClose}>Close</Button>}>
      <div className="vx-wsimg-col">
      <div className="vx-wsimg">
        <Identity kind="workspace" id={slug} slug={slug} name={name} size={56} />
        <div className="vx-wsimg-acts">
          <input ref={input} type="file" accept="image/svg+xml,image/png,image/webp" aria-label="Upload an image"
            className="vx-sr" disabled={busy} onChange={(e) => { const f = e.currentTarget.files?.[0]; if (f) void run(fileBody(f)); }} />
          <Button variant="primary" disabled={busy} onClick={() => input.current?.click()}>Upload…</Button>
          {st?.domain && <Button disabled={busy} onClick={() => void run({ use: "company_logo" })}>
            {`Use ${st.domain}'s logo`}</Button>}
          {st?.source === "workspace" && <Button variant="ghost" disabled={busy} onClick={() => void run({ reset: true })}>Reset</Button>}
          <span className="vx-wsimg-hint">Pick an icon below, or upload SVG, PNG or WebP up to 200 KB.</span>
          {note && <span role="status" className="vx-wsimg-hint">{note}</span>}
          {err && <span role="alert" className="vx-wsimg-err">{err}</span>}
        </div>
      </div>
      <GlyphPicker value={st?.source === "glyph" ? st.glyph : null} suggested={st?.glyph_by !== "person" ? st?.glyph : null}
        onPick={(g) => void run({ glyph: g })} />
      </div>
    </Dialog>
  );
}

/** The owner's one-click answer to an image an agent proposed. Renders nothing for anybody else. */
export function WorkspaceImageProposal({ slug, name }: { slug: string; name?: string }) {
  const [st, setSt] = useState<ImageState | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let on = true;
    readImageState(slug).then((s) => { if (on) setSt(s); }).catch(() => undefined);
    return () => { on = false; };
  }, [slug]);
  if (!st?.proposal || !st.owner) return null;
  const answer = async (accept: boolean) => {
    setBusy(true);
    try { await answerImageProposal(slug, accept); setSt({ ...st, proposal: null }); } catch { /* stays offered */ }
    finally { setBusy(false); }
  };
  return (
    <div className="vx-wsimg-proposal" data-image-proposal="">
      <Identity kind="workspace" id={slug} name={name ?? slug} src={`${base(slug)}/icon?proposal=1`} size={28} />
      <span className="vx-wsimg-hint">A new image was proposed for {name ?? "this workspace"}.</span>
      <Button variant="primary" disabled={busy} onClick={() => void answer(true)}>Accept</Button>
      <Button variant="ghost" disabled={busy} onClick={() => void answer(false)}>Decline</Button>
    </div>
  );
}
