/** What a click on an attention item or a page action does (#1783, R3 `home2`).
 *
 *  Every act runs AS THE PERSON WHO CLICKED, through doors the product already has — never a
 *  privileged path a page could borrow:
 *    · opening things: the shell's own events (a chat, a meeting, a page in the RIGHT panel — UI
 *      decision 7c: documents never open in the centre);
 *    · changing a record: its owner's write path (a proposal's resolve route, crm-api's card update);
 *    · anything the agent does (run a routine, prepare a meeting, schedule a call): the person's own
 *      chat, MCP-first — a visible line the person can read, and for scheduling a DRAFT they send
 *      themselves.
 *  An act with an effect beyond the person's own screen is confirmed first (`confirm`). The page
 *  action verbs are the fixed allow-list in `richParse.ACTION_VERBS`; nothing else is executable. */
import { WORKSPACE_IMAGE_CHANGED } from "../../../platform/identity";
import { ASK_CHAT_EVENT, CALL_START_EVENT, NEW_ROOM_EVENT, OPEN_MEETING_EVENT, OPEN_SESSION_EVENT } from "../../../platform/events";
import { canonicalVerb } from "../../readme/grammar";

/** The README's `{{invite}}` action asks the workspace page to open its Invite dialog. */
export const INVITE_EVENT = "vexa:workspace-invite";
import { VIEW_NAVIGATE_EVENT, viewSlotFor } from "../../roomView";
import { openCard, readCard, updateCard } from "../cardsApi";
import { toast } from "../../../ui-kit/primitives/Toast";
import type { ConfirmRequest } from "../../../ui-kit/primitives/Dialog";
import type { Act } from "./attentionApi";
import type { ActionVerb } from "./richParse";
import { attentionSnapshot, loadAttention } from "./attentionApi";

export const DESK = "personal";
const fire = (name: string, detail: unknown) => window.dispatchEvent(new CustomEvent(name, { detail }));

/** A page or README always opens in the right panel's view slot; the person's own desk has no slug. */
export function openDocOnRight(slug: string | undefined, path: string, label?: string): void {
  const ws = !slug || slug === DESK ? undefined : slug;
  const slot = viewSlotFor(ws, path);
  if (!slot.path || slot.path.split("/").includes("..")) return;
  window.dispatchEvent(new CustomEvent(VIEW_NAVIGATE_EVENT, { detail: label ? { ...slot, label } : slot }));
}

/** Say something in the person's own chat in front (visible), or put it in the composer as a draft. */
export function askAgent(prompt: string, opts: { draft?: boolean; display?: string; hidden?: boolean } = {}): void {
  fire(ASK_CHAT_EVENT, { prompt, ...(opts.display ? { display: opts.display } : {}), ...(opts.draft ? { mode: "draft" } : {}), ...(opts.hidden ? { hidden: true } : {}) });
}

/** Accept (or decline) a proposal through the route that owns it (agent-api's attention docstring):
 *  the desk's short list → `POST /api/proposals/resolve {id, status}`; a post-call proposal →
 *  `POST /api/sessions/{session}/proposals/{proposal}/accept|reject`. */
export async function resolveProposal(act: Act, accept: boolean): Promise<boolean> {
  const id = String(act.proposal ?? "");
  if (!id) return false;
  if (act.source === "identity" && act.slug) {
    try {
      const r = await fetch(`/api/workspaces/${encodeURIComponent(String(act.slug))}/identity/proposal`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ accept }) });
      if (!r.ok) { toast("That image could not be answered just now"); return false; }
      window.dispatchEvent(new CustomEvent(WORKSPACE_IMAGE_CHANGED, { detail: String(act.slug) }));
      toast(accept ? "The new image is the workspace's" : "Declined");
      void loadAttention({ force: true });
      return true;
    } catch { toast("That image could not be answered just now"); return false; }
  }
  const call = act.source === "call" && act.session;
  const url = call
    ? `/api/sessions/${encodeURIComponent(String(act.session))}/proposals/${encodeURIComponent(id)}/${accept ? "accept" : "reject"}`
    : "/api/proposals/resolve";
  const body = call ? {} : { id, status: accept ? "ran" : "dismissed" };
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { toast("That proposal could not be answered just now"); return false; }
    toast(accept ? "Accepted" : "Declined");
    void loadAttention({ force: true });
    return true;
  } catch {
    toast("That proposal could not be answered just now");
    return false;
  }
}

/** A mention, once opened, is read (`POST /api/notifications/read`). */
function markRead(notification: unknown): void {
  if (!notification) return;
  void fetch("/api/notifications/read", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [notification] }) })
    .then(() => loadAttention({ force: true })).catch(() => undefined);
}

export type Plan = { confirm?: ConfirmRequest; run: () => void | Promise<unknown> };

/** An attention item's act. */
export function planNeedAct(act: Act, name?: string): Plan {
  switch (act.kind) {
    case "open-session": return { run: () => { if (act.session) fire(OPEN_SESSION_EVENT, { session: act.session }); markRead(act.notification); } };
    case "open-meeting": return { run: () => { if (act.meeting) fire(OPEN_MEETING_EVENT, { ref: String(act.meeting) }); } };
    case "open-page": return { run: () => { if (act.path) openDocOnRight(act.slug, act.path); } };
    case "open-card": return { run: () => { if (act.card) openCard(act.card); } };
    case "prepare": return {
      run: () => {
        if (act.path) openDocOnRight(act.slug, act.path);
        else if (act.meeting) fire(OPEN_MEETING_EVENT, { ref: String(act.meeting) });
        askAgent(`Prepare me for ${name ?? "the upcoming call"}${act.meeting ? ` (meeting ${act.meeting})` : ""}: fill its agenda and what I should know.`, { draft: true });
      },
    };
    case "retry-routine": return {
      confirm: { title: "Run this routine again?", consequence: "It runs now as you, with everything it is allowed to do.", confirmLabel: "Run it now", tone: "primary" },
      // MCP-first: the agent retries it with its own tools, as the person (agent-api has no run route)
      run: () => askAgent(`Retry my routine ${act.routine ?? ""}${act.reaction ? ` (its failed run ${act.reaction})` : ""} now and tell me how it went.`, { display: `Retry ${name ?? "the routine"}` }),
    };
    case "accept-proposal":
      // a call's proposal is reviewed where it was made: its chat shows it with the text to edit
      if (act.source === "call" && act.session) return { run: () => { fire(OPEN_SESSION_EVENT, { session: act.session }); } };
      return {
        confirm: { title: "Accept this proposal?", consequence: "The change is made where it applies, as you.", confirmLabel: "Accept", tone: "primary" },
        run: () => resolveProposal(act, true),
      };
    default: return { run: () => undefined };
  }
}

/** What `POST /api/desktop/refs` answers for one reference, for THIS viewer. */
type Resolved = { kind?: string; visible?: boolean; name?: string; session?: string; meeting?: string; state?: string };

/** One typed reference, resolved for the person who clicked — never the page's word for it. */
async function resolveRef(key: string, workspace: string | undefined): Promise<Resolved | null> {
  try {
    const r = await fetch("/api/desktop/refs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refs: [key], ...(workspace ? { workspace } : {}) }) });
    if (!r.ok) return null;
    const got = ((await r.json()) as { refs?: Record<string, Resolved> })?.refs?.[key];
    return got && got.visible && typeof got.name === "string" && got.name.trim() ? got : null;
  } catch { return null; }
}

// A page's target is free text; each verb accepts only an id of its own shape, and only once the
// server resolves it for this viewer. Anything else is refused, with nothing sent.
const ROUTINE_ID = /^(?:flow:)?[\w.-]{1,80}$/;
const SESSION_ID = /^[\w.:-]{1,120}$/;
const RECORD_ID = /^[\w.-]{1,120}$/;
const STAGE = /^[\w .-]{1,60}$/;

function refused(): null { toast("This action is not available to you"); return null; }

/** A call this viewer's home reports live, by its chat session. */
async function liveSession(session: string): Promise<boolean> {
  await loadAttention();
  return Object.values(attentionSnapshot()?.workspaces ?? {}).some((w) => w?.live_call?.session === session);
}

async function startCallPlan(target: string, workspace: string | undefined): Promise<Plan | null> {
  if (!SESSION_ID.test(target)) return refused();
  const room = await resolveRef(`room:${target}`, workspace);
  if (!room) return refused();
  const session = room.session || target;
  return {
    confirm: { title: `Start a call in “${room.name}”?`, consequence: "Everyone in this room is rung.", confirmLabel: "Start the call", tone: "primary" },
    run: () => { fire(CALL_START_EVENT, { session }); },
  };
}

/** A page action from the allow-list. `target` is what the page wrote after the verb; the page's
 *  label is never used here — a confirm names what the server resolves for this viewer, and what
 *  the chat shows is exactly what is sent. `null` = refused (said in a toast). */
export async function planPageAction(verb: ActionVerb, target: string, workspace?: string): Promise<Plan | null> {
  const ws = workspace && workspace !== DESK ? workspace : DESK;
  // one registry (D17): `run-routine` is `run`, `schedule-call` is `schedule`
  switch (canonicalVerb(verb)) {
    case "run": {
      if (!ROUTINE_ID.test(target)) return refused();
      const routine = await resolveRef(`routine:${target}`, ws);
      if (!routine) return refused();
      const id = target;
      return {
        confirm: { title: `Run “${routine.name}”?`, consequence: "The routine runs now as you, with everything it is allowed to do.", confirmLabel: "Run it now", tone: "primary" },
        // no `display`: the person's chat shows exactly the line the agent gets
        run: () => askAgent(`Run my routine ${id} now and tell me how it went.`),
      };
    }
    case "start-call": return startCallPlan(target, ws);
    case "join-call": {
      const m = /^meeting:(.+)$/.exec(target);
      if (m) {
        if (!RECORD_ID.test(m[1])) return refused();
        const ref = m[1];
        return { run: () => { fire(OPEN_MEETING_EVENT, { ref }); } };
      }
      // only a call that is already live is joined without asking; anything else would START one
      if (SESSION_ID.test(target) && await liveSession(target)) return { run: () => { fire(CALL_START_EVENT, { session: target }); } };
      return startCallPlan(target, ws);
    }
    case "new-chat": return { run: () => askAgent(target || "", { draft: true }) };
    case "prepare": return { run: () => askAgent(`Prepare me for ${target || "the next call"}: fill its agenda and what I should know.`, { draft: true }) };
    case "new-room": return {
      confirm: { title: `Make a room${target ? ` “${target}”` : ""}?`, consequence: "Everyone in this workspace can find and join it.", confirmLabel: "Make the room", tone: "primary" },
      run: () => { window.dispatchEvent(new CustomEvent(NEW_ROOM_EVENT, { detail: { workspace: ws === DESK ? undefined : ws, name: target || undefined } })); },
    };
    case "invite": return { run: () => { window.dispatchEvent(new CustomEvent(INVITE_EVENT, { detail: { workspace: ws, role: target || undefined } })); } };
    case "ask": return { run: () => askAgent(target || "", { draft: true }) };
    case "schedule": return {
      // the agent drafts it; the person sends the words and its own tool confirms the invite
      confirm: { title: "Schedule a call?", consequence: "Vexa drafts the invitation in your chat; nothing is sent until you send it.", confirmLabel: "Draft it", tone: "primary" },
      run: () => askAgent(`Schedule a call${target ? ` for ${target}` : ""}: find a time that suits everyone and draft the invitation.`, { draft: true }),
    };
    case "accept-proposal": {
      // only a proposal on this viewer's own list, answered through the route that owns it
      if (!SESSION_ID.test(target)) return refused();
      await loadAttention();
      const need = (attentionSnapshot()?.needs ?? []).find((n) => n.act?.kind === "accept-proposal" && String(n.act.proposal ?? "") === target);
      if (!need) return refused();
      return {
        confirm: { title: `Accept “${need.text}”?`, consequence: "The change is made where it applies, as you.", confirmLabel: "Accept", tone: "primary" },
        run: () => resolveProposal(need.act, true),
      };
    }
    case "move-card": {
      const [id, ...rest] = target.split(":");
      const stage = rest.join(":").trim();
      if (!id || !RECORD_ID.test(id) || !STAGE.test(stage)) return refused();
      const card = await resolveRef(`card:${id}`, ws);
      if (!card) return refused();
      return {
        confirm: { title: `Move “${card.name}”?`, consequence: `It moves to “${stage}” for everyone on it.`, confirmLabel: "Move it", tone: "primary" },
        run: async () => {
          const rec = await readCard(id);
          if (!rec) { toast("That card is no longer available"); return; }
          await updateCard(rec, { transition: stage });
          void loadAttention({ force: true });
        },
      };
    }
    default: return null;
  }
}
