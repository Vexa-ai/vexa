/** WHAT A PERSON WROTE ABOUT THEMSELVES, AND WHAT YOUR ORG NOTES (feature `shell2`).
 *
 *  THE SEAM FOR S3 (`s3-person`: a profile is a claimed CRM record linked 1:1 to its user). Until it
 *  merges this reads `GET /api/people/{subject}/profile` and treats any refusal or 404 as "no
 *  profile yet" — the panel then shows the basics sharing a workspace allows. The answer's shape is
 *  the interface S3 is asked to serve; nothing here invents a field. CRM fields arrive only as the
 *  server filters them for the viewer (S1-C field classes), each marked when only you see it. */
export type ProfileLink = { kind: "linkedin" | "github" | "x" | "website"; url: string };
export type PersonProfile = {
  /** the person claimed their record (is on Vexa) */
  claimed: boolean;
  headline?: string; company?: string; bio?: string; location?: string;
  /** IANA zone, for their local time */
  timezone?: string; links?: ProfileLink[]; photo?: string;
  /** org notes, already filtered for this viewer */
  crm?: { org: string; fields: { label: string; value: string; only_you?: boolean }[] };
};

const SAFE = /^https:\/\//i;
export function sanitizeProfile(x: unknown): PersonProfile | null {
  if (!x || typeof x !== "object") return null;
  const o = x as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim().slice(0, 400) : undefined);
  const links = Array.isArray(o.links) ? (o.links as Record<string, unknown>[])
    .filter((l) => l && typeof l.url === "string" && SAFE.test(l.url as string) && ["linkedin", "github", "x", "website"].includes(String(l.kind)))
    .map((l) => ({ kind: l.kind as ProfileLink["kind"], url: String(l.url) })) : undefined;
  const crmRaw = o.crm as { org?: unknown; fields?: unknown } | undefined;
  const crm = crmRaw && Array.isArray(crmRaw.fields) ? { org: str(crmRaw.org) ?? "your organisation",
    fields: (crmRaw.fields as Record<string, unknown>[]).filter((f) => str(f.label) && str(f.value))
      .map((f) => ({ label: str(f.label)!, value: str(f.value)!, only_you: f.only_you === true })) } : undefined;
  return { claimed: o.claimed === true, headline: str(o.headline), company: str(o.company), bio: str(o.bio), location: str(o.location),
    timezone: str(o.timezone), links, photo: str(o.photo), crm };
}

export async function readPersonProfile(subject: string): Promise<PersonProfile | null> {
  try {
    const r = await fetch(`/api/people/${encodeURIComponent(subject)}/profile`, { cache: "no-store" });
    if (!r.ok) return null;
    return sanitizeProfile(await r.json());
  } catch { return null; }
}

/** Their local time now ("14:05"), or "" without a valid zone. */
export function localTime(tz: string | undefined, now = new Date()): string {
  if (!tz) return "";
  try { return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone: tz }).format(now); } catch { return ""; }
}
