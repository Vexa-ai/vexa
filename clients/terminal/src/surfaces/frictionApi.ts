/** frictionApi — "Report this" (PRD decision 33 §2; #1510).
 *
 *  Founder: *"we also need to leverage the mcp tool that should collect rough edges — things that
 *  did not work as expected — and dump it in a way that we can just dump that to an agent that
 *  would just fix that."* §1 is the agent filing its own; this is the half only a person can do —
 *  the agent cannot see that its answer was fine and the page it opened was the wrong one.
 *
 *  ONE LINE AND THE SURFACE, and nothing else is asked for. Every field the record needs beyond the
 *  sentence is something the client already knows: which chat, which page, which workspace, which
 *  meeting. Asking a person to classify their own complaint is how a report button ends up unused —
 *  the classifier is on the server (`shared/friction.py` infers `kind` from the words).
 *
 *  IT NEVER THROWS. `apiClient.getJson` is fail-loud by design and that is right for a document
 *  read; it is wrong here. A failure to report that the product is broken must not itself be an
 *  error dialog on top of the thing the person was already unhappy about — the caller shows
 *  "couldn't send that" beside the field and the text stays put.
 *
 *  #1510 CHANGED THE RESPONSE SHAPE. `/api/friction` no longer stores anything to read a count
 *  back from — it forwards onto the flows carrier (`control_plane.publish`), which is a single
 *  fact, not a deduplicated row (`shared/friction.py`'s module docstring says why the old status
 *  machine is gone). `{id, known, recurrence}` is now `{id, recorded}`; there is no more "known,
 *  Nth report" to say.
 */

/** The human surface, decision 30's shape as far as this client actually knows it. `chat` and
 *  `kind` come from the chat, `workspace`/`path` from the resolved view slot — never from a tab
 *  label or a breadcrumb (F63: those are display strings and two of them have already been wrong). */
export type FrictionSurface = {
  chat?: string;
  chatKind?: string;
  workspace?: string;
  path?: string;
  meeting?: string;
  /** what the person was looking at when they pressed it: a chat turn, or the open page */
  at?: "turn" | "page";
  /** the turn's own text, trimmed — what "this" refers to when they say "this is wrong" */
  quote?: string;
};

export type FrictionFiled = { id: string; recorded: boolean };

/** The session ref the flows carrier requires (#1510 — `POST /api/friction` refuses a report with
 *  none). A chat turn's report carries the chat id already; a page report with no chat open (the
 *  panel header's "Report this", outside any conversation) has nothing else to offer, so it names
 *  the surface itself rather than sending an empty string the route would refuse. */
const NO_CHAT_SESSION = "terminal";

/** The surface, read off the chat and whatever tab is active.
 *
 *  Structurally typed rather than importing `ActiveTab`: this module is data-access and the report
 *  only needs four strings. It reads the tab's PARAMS, which are the resolved view slot — never a
 *  label (F63). Unknown params are omitted, never guessed: a report naming the wrong page sends the
 *  fixing agent to the wrong file, which is worse than one naming no page at all. */
export function surfaceOf(
  chat: string,
  tab: { kind?: string; params?: Record<string, unknown> } | null | undefined,
): FrictionSurface {
  const p = (tab?.params ?? {}) as Record<string, unknown>;
  const str = (k: string) => (typeof p[k] === "string" ? (p[k] as string) : undefined);
  return { chat, chatKind: tab?.kind, workspace: str("slug"), path: str("path"), meeting: str("meetingId") };
}

/** How much of a turn travels as the quote. It is CONTEXT, not the report: the person's sentence is
 *  the report, and a whole agent turn pasted into `happened` would bury it. */
export const QUOTE_MAX = 600;

export async function reportFriction(line: string, surface: FrictionSurface): Promise<FrictionFiled | null> {
  const text = (line || "").trim();
  if (!text) return null;
  const body = {
    reporter: "person",
    session: surface.chat || NO_CHAT_SESSION,
    happened: text,
    tried: surface.at === "turn"
      ? "read this reply in the chat"
      : surface.path ? `opened ${surface.path}` : "used the terminal",
    context: {
      workspace: surface.workspace || "",
      path: surface.path || "",
      meeting_id: surface.meeting || "",
      surface: {
        chat: surface.chat || "",
        chat_kind: surface.chatKind || "",
        at: surface.at || "",
        quote: (surface.quote || "").slice(0, QUOTE_MAX),
      },
    },
  };
  try {
    const r = await fetch("/api/friction", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) return null;
    const j = (await r.json()) as { id?: string; recorded?: boolean };
    return { id: j.id || "", recorded: j.recorded !== false };
  } catch {
    return null;
  }
}

/** The confirmation, in the fewest words that still say something true. #1510: there is no more
 *  recurrence count to report (flows admits one row per occurrence, not a deduplicated row with a
 *  counter) — every successful file says the same thing. */
export function confirmation(filed: FrictionFiled | null): string {
  if (!filed) return "Couldn't send that — try again?";
  return "Filed. Thank you.";
}
