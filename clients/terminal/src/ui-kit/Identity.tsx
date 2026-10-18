/** <Identity> — the ONE component for everything that has a face (UI decisions 15f, 15g).
 *
 *  `kind` = person · company · workspace · agent. It draws, in order: the stored image (through the
 *  authed route the server gives — never a third party), else a deterministic monogram on the colour
 *  derived from `id` (`platform/identity`), the same on every load. A CRM person who is not a Vexa
 *  user is never a face: initials, plus their company's logo as a small badge. Agents and routines
 *  get a generated mark (one shape on their colour) until one is stored.
 *
 *  Shape says kind: people and agents are round, workspaces and companies are rounded squares.
 *  Colour is set by a custom property (`--id-h`) and `identity.css`, per theme; size by `--id-size`. */
import { useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { WORKSPACE_IMAGE_CHANGED, agentMark, identityColour, identitySrc, media, monogram, type IdentityKind } from "../platform/identity";
import "./identity.css";

/** URLs that answered with an error this session: not asked again on the next render. */
const FAILED = new Set<string>();

export type IdentityProps = {
  kind: IdentityKind;
  /** the stable id the colour and mark derive from (account id, workspace id, routine id, domain) */
  id: string;
  name: string;
  /** pixels; default 24 */
  size?: number;
  /** an image URL the server gives (a person's photo route, an agent's stored mark) */
  src?: string;
  /** a workspace's slug — its image route */
  slug?: string;
  /** media-proxy keys: a user's avatar, a company's logo (`d:<domain>`) */
  avatar?: string;
  logo?: string;
  /** a person who uses Vexa (only they may show a face) */
  user?: boolean;
  /** when given, the identity is a button: "Change photo" / "Change image" */
  onChange?: () => void;
  /** true: announce the name (role=img); default decorative, since the name is beside it */
  label?: boolean;
  className?: string;
  /** a small overlay (presence dot) */
  children?: ReactNode;
};

function Img({ url, onFail, cls }: { url: string; onFail: () => void; cls: string }) {
  return <img className={cls} src={url} alt="" draggable={false} loading="lazy" decoding="async"
    onError={() => { FAILED.add(url); onFail(); }} />;
}

const SHAPES = [
  <circle key="c" cx="12" cy="12" r="5.2" />,
  <path key="t" d="M12 6.2 18 16.8H6z" />,
  <path key="d" d="M12 5.6 18.4 12 12 18.4 5.6 12z" />,
  <rect key="s" x="7" y="7" width="10" height="10" rx="2.4" />,
  <path key="p" d="M10.4 6h3.2v4.4H18v3.2h-4.4V18h-3.2v-4.4H6v-3.2h4.4z" />,
  <path key="h" d="M12 5.5 17.6 8.75v6.5L12 18.5l-5.6-3.25v-6.5z" />,
];

export function Identity(p: IdentityProps) {
  const { kind, id, name, size = 24 } = p;
  const [version, setVersion] = useState(0);
  useEffect(() => {
    if (kind !== "workspace" || !p.slug || typeof window === "undefined") return;
    const on = (e: Event) => { if ((e as CustomEvent).detail === p.slug) setVersion((v) => v + 1); };
    window.addEventListener(WORKSPACE_IMAGE_CHANGED, on);
    return () => window.removeEventListener(WORKSPACE_IMAGE_CHANGED, on);
  }, [kind, p.slug]);
  const base = identitySrc(p);
  const want = base && version ? `${base}${base.includes("?") ? "&" : "?"}v=${version}` : base;
  const [failed, setFailed] = useState<string | null>(null);
  const url = want && !FAILED.has(want) && failed !== want ? want : undefined;
  const [, rerender] = useState(0);
  const mark = kind === "agent" ? agentMark(id) : null;
  const hue = mark ? mark.hue : identityColour(id).hue;
  const style = { "--id-size": `${size}px`, "--id-h": String(hue) } as CSSProperties;
  const badgeKey = kind === "person" && !p.user && p.logo ? media(p.logo) : undefined;
  const badge = badgeKey && !FAILED.has(badgeKey) ? badgeKey : undefined;
  const cls = `vx-id${p.onChange ? " vx-id-btn" : ""}${p.className ? ` ${p.className}` : ""}`;
  const a11y = p.onChange ? {} : p.label ? { role: "img", "aria-label": name } : { "aria-hidden": true as const };

  const face = url
    ? <Img url={url} cls="vx-id-img" onFail={() => setFailed(url)} />
    : mark
      ? <svg className="vx-id-mark" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{SHAPES[mark.shape]}</svg>
      : <span className="vx-id-mono">{monogram(name)}</span>;

  const inner = (
    <>
      {face}
      {badge && <span className="vx-id-badge" data-badge="">
        <Img url={badge} cls="vx-id-img" onFail={() => rerender((n) => n + 1)} />
      </span>}
      {p.children}
    </>
  );
  const data = { "data-kind": kind, "data-has-image": url ? "" : undefined };
  if (p.onChange) {
    const what = kind === "person" ? "Change photo" : "Change image";
    return <button type="button" className={cls} style={style} {...data} aria-label={`${what} — ${name}`}
      title={what} onClick={p.onChange}>{inner}</button>;
  }
  return <span className={cls} style={style} {...data} {...a11y}>{inner}</span>;
}

export type IdentityItem = Omit<IdentityProps, "size" | "onChange" | "children">;

/** Overlapping identities (avatar stack): at most `max`, then "+N". */
export function IdentityStack({ items, max = 4, size = 22 }: { items: IdentityItem[]; max?: number; size?: number }) {
  const shown = items.slice(0, max);
  const more = items.length - shown.length;
  return (
    <span className="vx-id-stack" style={{ "--id-size": `${size}px` } as CSSProperties}>
      {shown.map((it) => <Identity key={`${it.kind}:${it.id}`} {...it} size={size} />)}
      {more > 0 && <span className="vx-id vx-id-more" aria-label={`${more} more`}>+{more}</span>}
    </span>
  );
}

/** PERSON PHOTOS BY NAME: the people reads that carry a photo (identity's photo route — an upload,
 *  else the sign-in picture, else Gravatar) register it here, so every face drawn from a bare name
 *  (an AvatarStack, a person chip) shows the photo; no entry → the monogram. */
const PHOTOS = new Map<string, string>();
export function registerPersonPhotos(m: Record<string, string>): void { for (const [k, v] of Object.entries(m)) if (v) PHOTOS.set(k.trim().toLowerCase(), v); }
export const personPhoto = (name: string): string | undefined => PHOTOS.get(name.trim().toLowerCase());

/** AvatarStack (guidelines §4): the one way to show who was there — 20px faces through Identity
 *  (a photo when one is registered, else the monogram), at most `max` then "+N", a 2px ring in the
 *  surface colour, never clipped. The names are its accessible name and tooltip. */
export function AvatarStack({ names, max = 3, onOpen, label }: { names: string[]; max?: number;
  /** each face opens that person (a button named by them); without it the stack is one image */
  onOpen?: (name: string) => void;
  /** how a name reads (yourself as "you"); defaults to the name */
  label?: (name: string) => string }) {
  const shown = names.slice(0, max);
  const more = names.length - shown.length;
  const say = (n: string) => (label ? label(n) : n);
  const face = (n: string) => { const src = personPhoto(n); return <Identity key={n} kind="person" id={n} name={n} size={20} user={!!src} src={src} />; };
  if (onOpen) return (
    <span className="vx-avs" role="group" aria-label={names.map(say).join(", ")}>
      {shown.map((n) => <button key={n} type="button" className="vx-avs-btn" title={say(n)} aria-label={say(n)} onClick={(e) => { e.stopPropagation(); onOpen(n); }}>{face(n)}</button>)}
      {more > 0 && <span className="vx-id vx-id-more vx-avs-more" title={names.slice(max).map(say).join(", ")} aria-hidden>+{more}</span>}
    </span>
  );
  return (
    <span className="vx-avs" role="img" aria-label={names.map(say).join(", ")} title={names.map(say).join(", ")}>
      {shown.map(face)}
      {more > 0 && <span className="vx-id vx-id-more vx-avs-more" aria-hidden>+{more}</span>}
    </span>
  );
}
