/** Mock data for the terminal flows whose backend follows later (meetings/transcript/entities, git).
 *  Everything here is clearly fake + self-contained so the UX is fully demoable; swap for real
 *  transcript.v1 / runtime / git APIs as they land. */

export interface Participant { name: string; role: string; initials: string }
export interface ProposedAction { id: string; label: string; detail: string }
export interface TranscriptLine { t: string; speaker: string; text: string }
export interface MeetingMock {
  id: string;
  session_uid?: string;       // set on a LIVE-backend meeting → the tab subscribes to the real Stream
  title: string;
  when: string;
  status: "live" | "past";
  platform: string;
  participants: Participant[];
  mentioned: string[];          // workspace entity titles surfaced from the conversation
  actions: ProposedAction[];
  transcript: TranscriptLine[];
  insights: { t: string; text: string }[];  // copilot notes, revealed alongside the transcript
}

export const MEETINGS: MeetingMock[] = [
  {
    id: "gamestop",
    session_uid: "sess-gs-live",        // LIVE backend — transcript + cards stream from the dispatch
    title: "All-In — Ryan Cohen interview",
    when: "Now · live",
    status: "live",
    platform: "YouTube",
    participants: [
      { name: "Ryan Cohen", role: "Guest · GameStop / Chewy", initials: "RC" },
      { name: "Jason Calacanis", role: "Host", initials: "JC" },
      { name: "Chamath Palihapitiya", role: "Host", initials: "CP" },
      { name: "David Sacks", role: "Host", initials: "DS" },
      { name: "David Friedberg", role: "Host", initials: "DF" },
    ],
    mentioned: [],
    actions: [],
    transcript: [],     // streamed live
    insights: [],
  },
  {
    id: "mtg-acme-renewal",
    title: "Acme Corp — Renewal sync",
    when: "Now · live",
    status: "live",
    platform: "Google Meet",
    participants: [
      { name: "Jane Liu", role: "VP Sales · Acme", initials: "JL" },
      { name: "Raj Patel", role: "CTO · Acme", initials: "RP" },
      { name: "You", role: "Account lead", initials: "Y" },
    ],
    mentioned: ["Acme Corp", "Jane Liu", "Raj Patel", "Contract renewal", "SSO / SCIM"],
    actions: [
      { id: "a1", label: "Research Acme's latest funding", detail: "web + workspace" },
      { id: "a2", label: "Draft the renewal follow-up", detail: "to Jane, gated" },
      { id: "a3", label: "Confirm Raj leads onboarding", detail: "update Acme Corp" },
    ],
    transcript: [
      { t: "00:01", speaker: "Jane Liu", text: "Thanks for making time — we want to get the renewal closed this quarter." },
      { t: "00:14", speaker: "You", text: "Of course. I'll send updated pricing for the 250-seat tier by July 1." },
      { t: "00:29", speaker: "Raj Patel", text: "Our security team needs the SSO and SCIM details before we sign." },
      { t: "00:41", speaker: "Jane Liu", text: "And can you confirm Raj will lead the technical onboarding?" },
      { t: "00:55", speaker: "You", text: "Yes — Raj leads onboarding. I'll get the SSO docs over this week." },
      { t: "01:10", speaker: "Raj Patel", text: "Tuesday or Thursday next week works for a kickoff." },
    ],
    insights: [
      { t: "00:16", text: "Commitment detected — send 250-seat pricing by Jul 1. Want me to draft a task?" },
      { t: "00:31", text: "Acme needs SSO/SCIM docs to sign. This maps to the MVP5 enterprise features." },
      { t: "00:57", text: "Updating [[Acme Corp]]: Raj Patel → onboarding lead." },
    ],
  },
  {
    id: "mtg-standup",
    title: "Eng standup",
    when: "Today · 9:00",
    status: "past",
    platform: "Zoom",
    participants: [{ name: "You", role: "", initials: "Y" }, { name: "Team", role: "", initials: "T" }],
    mentioned: ["Prepare standup notes"],
    actions: [{ id: "b1", label: "File standup notes", detail: "workspace" }],
    transcript: [{ t: "00:02", speaker: "You", text: "Quick status round — what landed yesterday?" }],
    insights: [{ t: "00:05", text: "Recorded 3 action items as tasks." }],
  },
];

export const liveMeeting = () => MEETINGS.find((m) => m.status === "live");
export const meetingById = (id: string) => MEETINGS.find((m) => m.id === id);

// ── Knowledge-graph entities (mock until the workspace KG API lands) ─────────────────
export type EntityType = "person" | "company" | "topic" | "task";
export interface Entity {
  title: string;
  type: EntityType;
  path: string;            // workspace path
  exists: boolean;         // already a file in the workspace?
  subtitle: string;        // role / one-liner
  facts?: [string, string][];
  summary?: string;
  related?: string[];      // [[linked]] entity titles
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

export const ENTITIES: Record<string, Entity> = {
  "Jane Liu": { title: "Jane Liu", type: "person", path: "kg/entities/person/jane-liu.md", exists: true, subtitle: "VP Sales · Acme",
    facts: [["company", "[[Acme Corp]]"], ["role", "VP Sales"], ["email", "jane@acme.com"]],
    summary: "Primary commercial contact for the Acme renewal — owns the buying decision.", related: ["Acme Corp", "Raj Patel", "Contract renewal"] },
  "Raj Patel": { title: "Raj Patel", type: "person", path: "kg/entities/person/raj-patel.md", exists: true, subtitle: "CTO · Acme",
    facts: [["company", "[[Acme Corp]]"], ["role", "CTO"], ["leads", "onboarding"]],
    summary: "Technical decision-maker at Acme; will lead the post-sale onboarding.", related: ["Acme Corp", "SSO / SCIM"] },
  "You": { title: "You", type: "person", path: "kg/entities/person/you.md", exists: true, subtitle: "Account lead",
    facts: [["role", "Account lead"]], summary: "That's you — the account lead on this deal.", related: ["Acme Corp"] },
  "Acme Corp": { title: "Acme Corp", type: "company", path: "kg/entities/company/acme-corp.md", exists: true, subtitle: "Company · in renewal",
    facts: [["stage", "Renewal"], ["seats", "250"], ["owner", "[[You]]"], ["onboarding", "[[Raj Patel]]"]],
    summary: "Enterprise account up for renewal this quarter. Needs SSO/SCIM before signing.", related: ["Jane Liu", "Raj Patel", "Contract renewal", "SSO / SCIM"] },
  "Contract renewal": { title: "Contract renewal", type: "topic", path: "kg/entities/topic/contract-renewal.md", exists: false, subtitle: "Topic", related: ["Acme Corp"] },
  "SSO / SCIM": { title: "SSO / SCIM", type: "topic", path: "kg/entities/topic/sso-scim.md", exists: false, subtitle: "Topic", related: ["Acme Corp", "Raj Patel"] },
};

export function entityFor(title: string): Entity {
  return ENTITIES[title] ?? { title, type: "topic", path: `kg/entities/topic/${slug(title)}.md`, exists: false, subtitle: "Topic" };
}

/** split a meeting's people + mentioned entities into "in the room" vs "detected" (deduped). */
export function meetingEntities(m: MeetingMock): { present: Entity[]; detected: Entity[] } {
  const present = m.participants.map((p) => entityFor(p.name));
  const seen = new Set(present.map((e) => e.title));
  const detected = m.mentioned.filter((t) => !seen.has(t)).map(entityFor);
  return { present, detected };
}

/** Mock git state for the Files source-control section (until the workspace git API is exposed). */
export const GIT = {
  branch: "user/u_jane",
  changes: [
    { path: "kg/entities/task/prepare-standup-notes.md", kind: "A" as const },
    { path: "kg/entities/company/acme-corp.md", kind: "M" as const },
  ],
  commits: [
    { sha: "609f84f", msg: "Created task [[Prepare standup notes]]", when: "2m ago" },
    { sha: "b8414c0", msg: "Follow up with Jane Liu about Acme renewal", when: "1h ago" },
    { sha: "e59e95d", msg: "Created [[Raj Patel]]", when: "1h ago" },
  ],
};
