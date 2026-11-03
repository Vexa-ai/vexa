"use client";
/** `/p/<handle>` — a person's public profile (PRD 0.13.4 D35, founder addition). Off until the person
 *  switches it on; shows only the profile fields they marked public. A profile is the person's claimed
 *  CRM record, and nothing the org knows about them (notes, contact history, deals) is ever on it. */
import { useEffect, useState } from "react";
import { ExternalLink } from "../../../ui-kit/primitives/Links";
import "../profile.css";

type Links = { linkedin?: string; github?: string; x?: string; website?: string };
type Page = { handle: string; fields: { display_name?: string; headline?: string; company?: string; bio?: string;
  location?: string; timezone?: string; photo?: string; links?: Links } };

const LINK_LABEL: Record<keyof Links, string> = { linkedin: "LinkedIn", github: "GitHub", x: "X", website: "Website" };

export default function PublicProfile({ params }: { params: Promise<{ handle: string }> }) {
  const [page, setPage] = useState<Page | null>(null);
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    let live = true;
    void params.then(({ handle }) => fetch(`/api/p/${encodeURIComponent(handle)}`, { cache: "no-store" }))
      .then(async (r) => { if (!live) return; if (!r.ok) { setMissing(true); return; } setPage(await r.json()); })
      .catch(() => live && setMissing(true));
    return () => { live = false; };
  }, [params]);
  if (missing) return <main className="vx-public-profile"><p className="c-3">This profile is not public.</p></main>;
  if (!page) return <main className="vx-public-profile" aria-busy="true" />;
  const f = page.fields;
  return (
    <main className="vx-public-profile" data-public-profile={page.handle}>
      {f.display_name && <h1 className="vx-public-profile-name">{f.display_name}</h1>}
      {(f.headline || f.company) && <p className="vx-public-profile-line">{[f.headline, f.company].filter(Boolean).join(" · ")}</p>}
      {(f.location || f.timezone) && <p className="c-3">{[f.location, f.timezone].filter(Boolean).join(" · ")}</p>}
      {f.bio && <p className="vx-public-profile-bio">{f.bio}</p>}
      {f.links && (
        <ul className="vx-public-profile-links">
          {(Object.keys(LINK_LABEL) as (keyof Links)[]).filter((k) => f.links?.[k]).map((k) => (
            <li key={k}><ExternalLink href={f.links![k]!}>{LINK_LABEL[k]}</ExternalLink></li>
          ))}
        </ul>
      )}
    </main>
  );
}
