/** Synthetic cards for the card lab (`/debug/cards`). No real people, workspaces or data: every name
 *  is a placeholder. The stub answers `/api/desktop/card/<op>` (inbox, read, nonce, decide, seen),
 *  `/api/people/mentionable` and `/api/desktop` from this in-memory set, so the card components run
 *  exactly as they do against crm-api — without a backend. */
import type { CardAction, CoreCard } from "../../../platform/desktop";

const H = 3600_000;
const D = 24 * H;
const iso = (ms: number) => new Date(ms).toISOString();

export const ME = "p-me";
export const PEOPLE = [
  { subject: "p-ana", name: "Ana" },
  { subject: "p-sam", name: "Sam" },
  { subject: "p-lee", name: "Lee" },
];

function base(id: string, over: Partial<CoreCard>, now: number): CoreCard {
  return {
    id, ref: `card:${id}`, type: "commitment", revision: 1, title: "Card", about: null,
    state: "offered", states: ["offered", "acknowledged", "done"], side_states: [], next: null, waiting: null,
    summary: null, due: null, progress: null, workspaces: ["growth"], parent: null,
    children: { done: 0, total: 0, items: [] }, members: [], face: [], movement: null, stalled: false,
    can: { edit: false, transition: false, review: false, members: false }, href: `/c/${id}`,
    kind: "commitment", next_actor: ME, requester: "p-ana", origin: "session:lab-1", open: true,
    handoff: { phase: "offered", since: iso(now - 8 * H) },
    gates: [
      { name: "Offered", state: "done", by: "p-ana", at: iso(now - 8 * H) },
      { name: "Accepted", state: "current", by: null, at: iso(now - 8 * H) },
      { name: "Done", state: "ahead", by: null, at: null },
    ],
    viewer: { role: "next_actor", seen: false, actions: ["acknowledge", "decline"] },
    decisions: [], last_decision: null, chain: { blocks: [], blocked_by: [] }, triage: null,
    ...over,
  } as CoreCard;
}

const LONG_DIFF = [
  "- Replies to the onboarding thread go out within one working day",
  "+ Replies to the onboarding thread go out within four working hours",
  "- Escalate to the owner after two days",
  "+ Escalate to the owner after one day",
  "  Weekends are excluded",
  "+ Public holidays in the workspace's region are excluded",
  "+ A reply drafted by an agent is reviewed before it is sent",
  "  The workspace owner can change this rule",
  "+ Every change to this rule is a signed card",
].join("\n");

export function makeCards(now = Date.now()): CoreCard[] {
  return [
    base("lab-commit", {
      title: "Send the agenda to the room", kind: "commitment", due: iso(now + 1 * D),
      summary: "Send the agenda to the room by Thursday.",
      triage: { by: "Triage", suggest: "accept", because: "you sent last week's agenda" },
    }, now),
    base("lab-approve", {
      title: "Approve the reply-time rule for the onboarding thread so new members hear back within four working hours",
      type: "approval", kind: "approval", revision: 4, summary: LONG_DIFF, due: iso(now + 2 * D),
      viewer: { role: "next_actor", seen: true, actions: ["decide", "decline"] },
      gates: [
        { name: "Proposed", state: "done", by: "p-sam", at: iso(now - 26 * H) },
        { name: "Approved", state: "current", by: null, at: iso(now - 26 * H) },
        { name: "In force", state: "ahead", by: null, at: null },
      ],
      handoff: { phase: null, since: iso(now - 26 * H) }, requester: "p-sam",
      chain: { blocks: ["lab-deliver"], blocked_by: [] },
      decisions: [{ revision: 2, action: "acknowledge", by: "p-sam", at: iso(now - 26 * H) }],
    }, now),
    base("lab-policy", {
      title: "Let the notes routine write to the shared workspace", type: "policy_change", kind: "policy_change",
      summary: "The notes routine may write summaries into the Growth workspace, readable by its 4 members.",
      viewer: { role: "next_actor", seen: false, actions: ["decide", "decline"] }, requester: "p-lee",
    }, now),
    base("lab-question", {
      title: "Which week works for the quarterly review?", type: "question", kind: "question",
      summary: "Two options: the week of the 26th or the week of 2 November.",
      viewer: { role: "next_actor", seen: false, actions: ["answer", "decline"] }, requester: "p-lee",
    }, now),
    base("lab-deliver", {
      title: "Share the pricing sheet with finance", type: "ask", kind: "ask", due: iso(now + 5 * D),
      summary: "Finance needs the current sheet before the planning call.",
      face: [
        { type: "text", text: "Attach the sheet or link to where it lives." },
        { type: "locked", label: "Budget" },
      ],
      viewer: { role: "next_actor", seen: false, actions: ["deliver", "decline"] }, requester: "p-ana",
      chain: { blocks: [], blocked_by: ["lab-approve"] },
    }, now),
    // the viewer asked: it waits on Ana
    base("lab-mine-waiting", {
      title: "Review the launch checklist", kind: "commitment", next_actor: "p-ana", requester: ME,
      viewer: { role: "requester", seen: true, actions: [] }, workspaces: ["launch"],
    }, now),
    // the viewer only watches; due soon
    base("lab-watch", {
      title: "Confirm the venue booking", type: "approval", kind: "approval", next_actor: "p-sam", requester: "p-lee",
      due: iso(now + 20 * H), handoff: { phase: null, since: iso(now - 3 * D) },
      viewer: { role: "watcher", seen: true, actions: [] },
    }, now),
    // decided: folded
    base("lab-signed", {
      title: "Sign the data-processing addendum", kind: "commitment", open: false, state: "done",
      viewer: { role: "requester", seen: true, actions: [] }, next_actor: null, requester: ME,
      last_decision: { action: "confirm", by: "p-ana", at: iso(now - 2 * D) },
      decisions: [{ revision: 3, action: "confirm", by: "p-ana", at: iso(now - 2 * D) }],
    }, now),
    base("lab-declined", {
      title: "Move the standup to 8:00", kind: "question", open: false, state: "declined",
      viewer: { role: "member", seen: true, actions: [] }, next_actor: null,
      last_decision: { action: "decline", by: "p-lee", at: iso(now - 5 * H) },
      decisions: [{ revision: 2, action: "decline", by: "p-lee", at: iso(now - 5 * H) }],
    }, now),
  ];
}

export type LabCall = { op: string; body: Record<string, unknown>; at: number };

/** The in-memory crm-api: answers the card verbs over `makeCards()` and records every call. */
export function makeStub(now = Date.now()) {
  let cards = makeCards(now);
  const calls: LabCall[] = [];
  const counts = () => ({
    waiting_on_me: cards.filter((c) => c.open !== false && c.viewer?.role === "next_actor").length,
    due_soon: cards.filter((c) => c.open !== false && c.due && Date.parse(c.due) - Date.now() < 3 * D).length,
    i_wait_on: cards.filter((c) => c.open !== false && c.viewer?.role === "requester").length,
  });
  const find = (id: unknown) => cards.find((c) => c.id === id) ?? null;
  const answer = (op: string, body: Record<string, unknown>): { status: number; body: unknown } => {
    calls.push({ op, body, at: Date.now() });
    if (op === "inbox") return { status: 200, body: { items: cards, counts: counts() } };
    const c = find(body.card);
    if (!c) return { status: 404, body: { detail: "That card is no longer available" } };
    if (op === "read") return { status: 200, body: { card: c } };
    if (op === "seen") { c.viewer = { ...c.viewer!, seen: true }; return { status: 200, body: { status: "saved" } }; }
    if (op === "nonce") return { status: 200, body: { nonce: `n-${c.id}-${c.revision}-${calls.length}`, content_hash: `h-${c.id}-${c.revision}`, revision: c.revision } };
    if (op === "decide") {
      const action = body.action as CardAction;
      if (!c.viewer?.actions.includes(action)) return { status: 403, body: { detail: "You can't decide this card" } };
      if (body.content_hash !== `h-${c.id}-${c.revision}`) return { status: 409, body: {} };
      const at = new Date().toISOString();
      const revision = c.revision + 1;
      const next: CoreCard = {
        ...c, revision, open: false, state: action === "decline" ? "declined" : "done",
        viewer: { ...c.viewer, actions: [] }, next_actor: null,
        last_decision: { action, by: ME, at },
        decisions: [...(c.decisions ?? []), { revision, action, by: ME, at }],
      };
      cards = cards.map((x) => (x.id === c.id ? next : x));
      return { status: 200, body: { status: "saved", card: next } };
    }
    return { status: 404, body: {} };
  };
  return { answer, calls, reset: () => { cards = makeCards(); calls.length = 0; } };
}

export const DESKTOP = {
  version: 1, undo_version: null, proposals: [], filters: [], saved: [], hidden_workspaces: 0,
  presence_visibility: "workspace", cards_on: true,
  sections: [{ id: "workspaces", title: "Workspaces", items: [
    { kind: "workspace", ref: "workspace:growth", slug: "growth", name: "Growth", workspace_kind: "team", members: [] },
    { kind: "workspace", ref: "workspace:launch", slug: "launch", name: "Launch", workspace_kind: "team", members: [] },
  ] }],
};

/** chat text with ```card fences, rendered through MdxDoc exactly as an agent's message is */
export const CHAT_DOC = [
  "Two cards need you before the review:",
  "",
  "```card",
  '{"card":"lab-approve"}',
  "```",
  "",
  "```card",
  '{"card":"lab-mine-waiting"}',
  "```",
  "",
  "And one that was settled already:",
  "",
  "```card",
  '{"card":"lab-signed"}',
  "```",
].join("\n");
