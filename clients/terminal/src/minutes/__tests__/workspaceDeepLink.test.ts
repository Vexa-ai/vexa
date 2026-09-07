/** `/w/<workspace>/<path>` — the link that opens a page (Vexa-ai/vexa#1643).
 *
 *  **Seen on the dogfood stack:** the admin opened `/w/oenb-b5e60c/README.md` — a shared workspace
 *  he owns — and the terminal started a new chat and showed HIS DESK's README. The URL was never
 *  recognised as a route (the ref is a slug; only ids parsed), and nothing downstream could tell
 *  *"I could not open that"* from *"here is the usual page"*.
 *
 *  So the property under test is one sentence: **a link either opens the page it names, or says
 *  one sentence about why not.** Never the desk instead, and never silence. Three kinds of
 *  workspace can be named — a desk, a shared workspace, the company layer — and each of them is
 *  addressable by its id and by its slug, because the id is what a canonical link carries and the
 *  slug is what a person pastes.
 */
import { describe, expect, it, vi } from "vitest";

import {
  chatForDeepLink, deepLinkOutcome, refusalSentence, resolveWorkspaceDeepLink,
} from "../deepLink";
import { workspaceRouteFromPath, workspacePath, isWorkspaceRouteRef } from "../../app/workspaceRoute";
import type { WorkspaceIdentity } from "../../surfaces/workspaceApi";
import { newChat, type Chat } from "../chats";

const ID = "k4m5x2q7bd";                       // 10 chars of base32 — the canonical form
const readable = (over: Partial<WorkspaceIdentity> = {}): WorkspaceIdentity =>
  ({ id: ID, name: "Pilot", kind: "group", slug: "pilot-b5e60c", access: "readable", ...over });

// ── the route parses both names a workspace has ─────────────────────────────────────────────────
describe("what the route reads out of the address bar", () => {
  it("takes the canonical id AND the slug a person pastes", () => {
    expect(workspaceRouteFromPath(`/w/${ID}/README.md`)).toEqual({ workspace: ID, path: "README.md" });
    // the exact shape of the founder's report — a slug, with a dash and a hex tail
    expect(workspaceRouteFromPath("/w/pilot-b5e60c/README.md"))
      .toEqual({ workspace: "pilot-b5e60c", path: "README.md" });
    // a desk is addressed by the subject id it lives under; the company layer by `_global`
    expect(workspaceRouteFromPath("/w/126/kg/entities/olga.md"))
      .toEqual({ workspace: "126", path: "kg/entities/olga.md" });
    expect(workspaceRouteFromPath("/w/_global/POLICIES.md"))
      .toEqual({ workspace: "_global", path: "POLICIES.md" });
  });

  it("still refuses a traversal, a dot-namespaced tree and a non-route", () => {
    expect(workspaceRouteFromPath("/w/pilot-b5e60c/../secrets")).toBeNull();
    expect(workspaceRouteFromPath("/w/.system/x.md")).toBeNull();     // machinery is not addressable
    expect(workspaceRouteFromPath("/w/")).toBeNull();
    expect(workspaceRouteFromPath("/meetings/12")).toBeNull();
    expect(isWorkspaceRouteRef("a/b")).toBe(false);
    expect(workspacePath("../etc", "x")).toBe("/");
  });
});

// ── the three kinds, and what opens ─────────────────────────────────────────────────────────────
describe("a readable workspace opens the page the link names", () => {
  it("a SHARED workspace — the founder's own report", () => {
    const out = deepLinkOutcome({ workspace: "pilot-b5e60c", path: "README.md" }, readable());
    expect(out).toEqual({
      kind: "open", workspace: "pilot-b5e60c",
      // a workspace's README is its FRONT PAGE, so the tab wears the workspace's name (#1623),
      // never "README" and never the directory name (F49)
      page: { path: "README.md", slug: "pilot-b5e60c", label: "Pilot" },
    });
  });

  it("a DESK — addressed by the slug it lives under, named by the registry", () => {
    const out = deepLinkOutcome({ workspace: "126", path: "kg/entities/olga.md" },
                                readable({ kind: "desk", name: "Desk 126", slug: "126" }));
    expect(out).toEqual({
      kind: "open", workspace: "126",
      page: { path: "kg/entities/olga.md", slug: "126", label: "olga" },
    });
  });

  it("the COMPANY LAYER — and a ref with no path opens the front page", () => {
    const out = deepLinkOutcome({ workspace: "_global", path: "" },
                                readable({ kind: "global", name: "The organisation", slug: "_global" }));
    expect(out).toEqual({
      kind: "open", workspace: "_global",
      page: { path: "README.md", slug: "_global", label: "The organisation" },
    });
  });
});

// ── and when it cannot open, ONE SENTENCE ───────────────────────────────────────────────────────
describe("a link that cannot open says why, and opens nothing", () => {
  it("a workspace that is not this reader's", () => {
    const out = deepLinkOutcome({ workspace: ID, path: "README.md" },
                                readable({ access: "not-yours" }));
    expect(out).toEqual({
      kind: "refused",
      sentence: "That page is in Pilot, a workspace you do not have access to.",
    });
  });

  it("a workspace that is gone, and one we could not ask about", () => {
    expect(refusalSentence({ id: "", name: null, kind: null, access: "gone" }))
      .toBe("That link points at a workspace that is no longer here.");
    // a lookup that FAILED is a fourth state and says so — "could not find out" is temporary
    expect(refusalSentence(null))
      .toBe("That link could not be checked just now — try it again in a moment.");
    expect(deepLinkOutcome({ workspace: ID, path: "x.md" }, null).kind).toBe("refused");
  });

  it("never falls back to the desk — a refusal carries no page at all", () => {
    const out = deepLinkOutcome({ workspace: ID, path: "README.md" }, readable({ access: "not-yours" }));
    expect("page" in out).toBe(false);
  });
});

// ── which door the ref goes through ─────────────────────────────────────────────────────────────
describe("resolving the ref", () => {
  const lookup = () => ({
    byId: vi.fn(async () => readable()),
    bySlug: vi.fn(async () => readable()),
  });

  it("an id is resolved by id; anything else by slug", async () => {
    const a = lookup();
    await resolveWorkspaceDeepLink({ workspace: ID, path: "" }, a);
    expect(a.byId).toHaveBeenCalledWith(ID);
    expect(a.bySlug).not.toHaveBeenCalled();

    const b = lookup();
    await resolveWorkspaceDeepLink({ workspace: "pilot-b5e60c", path: "" }, b);
    expect(b.bySlug).toHaveBeenCalledWith("pilot-b5e60c");
    expect(b.byId).not.toHaveBeenCalled();
  });

  it("a lookup that throws becomes the sentence, never an unhandled rejection", async () => {
    const out = await resolveWorkspaceDeepLink({ workspace: ID, path: "" }, {
      byId: async () => { throw new Error("network"); },
      bySlug: async () => readable(),
    });
    expect(out).toEqual({
      kind: "refused",
      sentence: "That link could not be checked just now — try it again in a moment.",
    });
  });
});

// ── and WHICH CHAT it lands in ──────────────────────────────────────────────────────────────────
describe("a link never starts a new chat when the viewer has chats", () => {
  const at = (c: Chat, t: number): Chat => ({ ...c, lastActivityAt: t });
  const aimed = at({ ...newChat("Pilot work", ["personal", "_global", "pilot-b5e60c"]), target: "pilot-b5e60c" }, 10);
  const mounted = at(newChat("Somewhere", ["personal", "_global", "pilot-b5e60c"]), 20);
  const recent = at(newChat("Latest", ["personal", "_global"]), 30);

  it("prefers the chat already AIMED at that workspace", () => {
    expect(chatForDeepLink([recent, mounted, aimed], "pilot-b5e60c")?.id).toBe(aimed.id);
  });

  it("else one that has it mounted, else simply the most recent", () => {
    expect(chatForDeepLink([recent, mounted], "pilot-b5e60c")?.id).toBe(mounted.id);
    expect(chatForDeepLink([recent, at(newChat("Older", ["personal"]), 1)], "pilot-b5e60c")?.id).toBe(recent.id);
    expect(chatForDeepLink([recent, mounted, aimed], undefined)?.id).toBe(recent.id);
  });

  it("and answers null only when there is genuinely nothing to land in", () => {
    expect(chatForDeepLink([], "pilot-b5e60c")).toBeNull();
  });
});
