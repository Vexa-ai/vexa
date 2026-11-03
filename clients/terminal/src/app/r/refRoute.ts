/** `/r/<kind>/<id>` — where a universal reference lands (#1783). Pure: the server's answer → where to
 *  go, or the one sentence to say. Access is the server's answer, never the URL's. */
import type { RefAnswer } from "../../data/workspaceApi";

export const OPEN_SESSION_KEY = "vexa.openSession";

export type RefLanding = { go: string; session?: string } | { say: string; ask?: boolean };

export function refLanding(a: RefAnswer | null): RefLanding {
  if (!a) return { say: "That link could not be checked just now — try it again in a moment." };
  if (a.access === "not-yours") return { say: "You don't have access to this.", ask: a.request_access };
  if (a.access === "gone") return { say: "That link points at something that is no longer here." };
  if (a.access === "unavailable") return { say: "That isn't available to you." };
  const o = (a.open ?? {}) as Record<string, string | number | undefined>;
  if (o.join) return { go: `/join/${o.join}` };
  if (o.chat) return { go: "/", session: String(o.chat) };
  if (o.meeting !== undefined) return { go: `/meetings/${o.meeting}` };
  if (a.kind === "workspace") return { go: `/w/${a.id}` };
  if (o.path) return { go: `/w/${a.id.split("/")[0]}/${String(o.path)}` };
  return { go: "/" };
}
