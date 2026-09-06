/** THE WORKSPACE FRONT PAGE IS A HEADER STRIP (Vexa-ai/vexa#1628), AND THE STRIP IS TWO SENTENCES
 *  (Vexa-ai/vexa#1634).
 *
 *  Founder, 2026-09-06, on `_global/README.md`, looking at what #1623 had just built: *"ups, the
 *  workspace panel should take only the header, 1/8 screen at max, the rest collapsed."* Then, on
 *  what #1628 made of it: *"what about this one? never spoke about how to make it right, helpful and
 *  nice."*
 *
 *  Both rulings are held here, because the second did not replace the first — it moved everything
 *  the first sized down BEHIND ONE DISCLOSURE, and the size rule has to keep holding on the two
 *  lines that are left. So the claims are:
 *
 *    · THE HEIGHT IS BOUNDED BY THE VIEWPORT, not by how little this particular workspace happens to
 *      have in it. A panel that fits today because the roster is empty is not a panel that obeys the
 *      rule; the cap has to be in the style, against `vh`, and it has to be inside 1/8.
 *    · NOTHING IS OPEN BY DEFAULT — and *closed* means the content is not in the document at all,
 *      not that it is hidden with CSS. Since #1634 that includes the six SUMMARIES themselves: the
 *      front page is two sentences and its acts, and nothing else.
 *    · A DISCLOSURE OPENS AND CLOSES, and opening the next one puts the first away: two open
 *      sections are the beginning of the wall #1628 exists to remove.
 *    · THE POSTURE IS REMEMBERED PER WORKSPACE AND IN THE BROWSER ONLY. Which section a person had
 *      open is not a fact about the workspace and never reaches the server.
 *
 *  Rendered through `PagesPanel`, like the panel's own tests, because the claim is about what a
 *  reader meets on the page — not about a component in isolation.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import * as api from "../../surfaces/workspaceApi";

vi.mock("../../surfaces/workspaceApi", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../surfaces/workspaceApi")>()),
  readWorkspaceBySlug: vi.fn(),
  listWorkspaceTree: vi.fn(),
  readWorkspaceHistory: vi.fn(),
  readLastChange: vi.fn(),
  readMyPerson: vi.fn(),
  gitRemoteStatus: vi.fn(),
  readWorkspaceFile: vi.fn(),
  listSharedMemberships: vi.fn(),
  listWorkspaceMembers: vi.fn(),
  readInstanceAdmin: vi.fn(),
  mintInvite: vi.fn(),
}));

import { ASK_CHAT_EVENT } from "../../canvas/actions";
import { PagesPanel } from "../PagesPanel";
import { STRIP_MAX_VH } from "../WorkspaceReadmePanel";
import type { Page } from "../types";

const COMMITS = [
  { sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", author: "126", kind: "you" as const, files: ["README.md"] },
];

const CHANGE = {
  slug: "pilot-b5e60c", path: null,
  change: {
    sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", ts: 0,
    kind: "member" as const, author: "Jane Smith",
    pages: [{ path: "kg/board.md", title: "the governing board" }], count: 1,
    files: ["kg/board.md"],
  },
};

const READ_ME: Page[] = [{ path: "README.md", slug: "pilot-b5e60c", label: "Pilot" }];

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={READ_ME} docPath="README.md" docSlug="pilot-b5e60c" onOpen={() => {}}
    body={"# Pilot\n\nThe workspace body."} {...over} />);

const strip = (c: HTMLElement) => c.querySelector("[data-ws-strip]") as HTMLElement | null;
const sections = (c: HTMLElement) => c.querySelector("[data-ws-sections]") as HTMLElement | null;
const disclosures = (c: HTMLElement) => [...c.querySelectorAll<HTMLButtonElement>("[data-ws-disclosure]")];
const openIds = (c: HTMLElement) => [...c.querySelectorAll("[data-ws-section]")].map((s) => s.getAttribute("data-ws-section"));
const acts = (c: HTMLElement) => [...c.querySelectorAll<HTMLButtonElement>("[data-ws-strip-act]")];

/** THE ONE DISCLOSURE (#1634 rule 6) — History, at the end of line two. Everything #1628 built is
 *  behind it, so every test about a section starts by pressing it. */
const openDetails = async (c: HTMLElement) => {
  await waitFor(() => expect(c.querySelector("[data-ws-details]")).toBeTruthy());
  fireEvent.click(c.querySelector("[data-ws-details]")!);
  await waitFor(() => expect(disclosures(c).length).toBe(6));
};

beforeEach(() => {
  window.localStorage.clear();
  vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "Pilot", kind: "group", slug: "pilot-b5e60c", access: "readable", writable: false });
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md", "kg/entities/acme.md"]);
  vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "pilot-b5e60c", branch: "main", path: null, limit: 11, commits: COMMITS });
  vi.mocked(api.readLastChange).mockResolvedValue(CHANGE);
  vi.mocked(api.readMyPerson).mockResolvedValue({ subject: "126", name: "Alex Roe", first_name: "Alex" });
  vi.mocked(api.readInstanceAdmin).mockResolvedValue({ name: "Jane Smith", first_name: "Jane" });
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: true, remote: "origin", url: "https://github.com/pilot/kg", branch: "main", tracked: true, ahead: 2, behind: 0 });
  vi.mocked(api.readWorkspaceFile).mockResolvedValue("# Policies\n");
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "owner" }]);
  vi.mocked(api.listWorkspaceMembers).mockResolvedValue([{ subject: "126", role: "owner", email: "jsmith@example.com" }]);
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, json: async () => (String(url).includes("/api/auth/me") ? { is_admin: false } : []),
  })) as unknown as typeof fetch);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); });

describe("the strip takes the header and no more", () => {
  it("caps itself against the VIEWPORT, and the cap is inside the founder's 1/8", async () => {
    // A viewport this test states rather than inherits: the claim is a fraction of the screen, so a
    // jsdom default of 768 would be measuring a coincidence.
    Object.defineProperty(window, "innerHeight", { value: 900, configurable: true });

    const { container } = panel();
    await waitFor(() => expect(strip(container)).toBeTruthy());

    const s = strip(container)!;
    expect(s.style.maxHeight).toBe(`${STRIP_MAX_VH}vh`);      // the cap is in the style, not in luck
    expect(s.style.overflowY).toBe("auto");                   // …and nothing is clipped out of reach
    // …and the cap the style names is inside "1/8 screen at max" on the viewport above
    expect((STRIP_MAX_VH / 100) * window.innerHeight).toBeLessThanOrEqual(window.innerHeight / 8);
  });

  it("renders NOTHING but the two sentences and the acts until the disclosure is opened", async () => {
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBeGreaterThan(0));

    expect(openIds(container)).toEqual([]);
    // not hidden — absent: since #1634 the six summaries are behind the disclosure too
    expect(sections(container)).toBeNull();
    expect(disclosures(container)).toHaveLength(0);
    expect(container.querySelector("[data-ws-history]")).toBeNull();
    expect(container.querySelector("[data-ws-member]")).toBeNull();
    expect(container.querySelector('[data-ws-fact="remote"]')).toBeNull();
    expect(container.querySelector("[data-ws-details]")!.getAttribute("aria-expanded")).toBe("false");
    // …while the two sentences have already said where you are and what last happened
    expect(container.querySelector('[data-ws-line="where"]')!.textContent).toBeTruthy();
    // The person is the SUBJECT of the sentence now (#1634's design spec, point 4) — the colon
    // form was a log entry with words around it.
    expect(container.querySelector("[data-ws-changed]")!.textContent)
      .toBe("Jane Smith changed the governing board page 2 hours ago");
  });

  it("opens the section its summary belongs to, closes it on a second click, and holds one at a time", async () => {
    const { container } = panel();
    await openDetails(container);

    fireEvent.click(container.querySelector('[data-ws-disclosure="github"]')!);
    expect(openIds(container)).toEqual(["github"]);
    await waitFor(() => expect(container.querySelector('[data-ws-fact="remote"]')).toBeTruthy());
    expect(container.querySelector('[data-ws-disclosure="github"]')!.getAttribute("aria-expanded")).toBe("true");

    // a second disclosure REPLACES it — two open sections are the wall #1628 removed
    fireEvent.click(container.querySelector('[data-ws-disclosure="shared"]')!);
    expect(openIds(container)).toEqual(["shared"]);
    expect(container.querySelector('[data-ws-fact="remote"]')).toBeNull();

    // …and clicking the open one closes it
    fireEvent.click(container.querySelector('[data-ws-disclosure="shared"]')!);
    expect(openIds(container)).toEqual([]);
  });

  it("puts the whole of #1628's panel away again when History is pressed a second time", async () => {
    const { container } = panel();
    await openDetails(container);

    fireEvent.click(container.querySelector("[data-ws-details]")!);

    expect(sections(container)).toBeNull();
    expect(openIds(container)).toEqual([]);
    expect(container.querySelector("[data-ws-details]")!.getAttribute("aria-expanded")).toBe("false");
  });
});

describe("the open section is remembered per workspace, in the browser only", () => {
  it("comes back on the next visit to the SAME workspace, and nothing is open on another", async () => {
    const first = panel();
    await openDetails(first.container);
    fireEvent.click(first.container.querySelector('[data-ws-disclosure="pages"]')!);
    expect(openIds(first.container)).toEqual(["pages"]);
    cleanup();

    const again = panel();
    await waitFor(() => expect(openIds(again.container)).toEqual(["pages"]));
    cleanup();

    // another workspace is another posture — and its default is still nothing open
    vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w2", name: "Acme", kind: "group", slug: "acme-1", access: "readable", writable: false });
    const other = panel({ pages: [{ path: "README.md", slug: "acme-1", label: "Acme" }], docSlug: "acme-1" });
    await waitFor(() => expect(other.container.querySelector("[data-ws-details]")).toBeTruthy());
    expect(openIds(other.container)).toEqual([]);
    expect(sections(other.container)).toBeNull();
  });

  it("writes the posture nowhere but this browser", async () => {
    const { container } = panel();
    await openDetails(container);

    fireEvent.click(container.querySelector('[data-ws-disclosure="pages"]')!);

    expect(window.localStorage.getItem("vexa.wsreadme.open:pilot-b5e60c")).toBe("pages");
    // the server was asked nothing: every call this panel made was a READ of the workspace
    const posted = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .filter((c) => (c[1] as RequestInit | undefined)?.method && (c[1] as RequestInit).method !== "GET");
    expect(posted).toEqual([]);
  });
});

describe("the section summaries are one tab stop with buttons inside them", () => {
  it("puts exactly one disclosure in the tab order, and the arrows move it", async () => {
    const { container } = panel();
    await openDetails(container);

    expect(sections(container)!.getAttribute("role")).toBe("toolbar");
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([0, -1, -1, -1, -1, -1]);
    expect(disclosures(container).every((b) => b.tagName === "BUTTON")).toBe(true);
    // the category each summary answers is in its accessible name, not printed beside it
    expect(disclosures(container).map((b) => b.getAttribute("aria-label")?.split(":")[0]))
      .toEqual(["Kind", "Pages", "Last change", "Shared with", "GitHub", "History"]);

    fireEvent.keyDown(sections(container)!, { key: "ArrowRight" });
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([-1, 0, -1, -1, -1, -1]);

    fireEvent.keyDown(sections(container)!, { key: "End" });
    expect(disclosures(container).map((b) => b.tabIndex)).toEqual([-1, -1, -1, -1, -1, 0]);
  });
});

describe("the summaries still say the six things, each as an answer", () => {
  it("kind · pages · last change · shared with · the repo · a commit count", async () => {
    const { container } = panel();
    await openDetails(container);

    const say = (id: string) => container.querySelector(`[data-ws-disclosure="${id}"]`)?.textContent ?? "";
    expect(say("this")).toContain("Shared workspace");
    expect(say("pages")).toContain("2 pages");
    expect(say("last")).toContain("readme: link the entity");
    // …and the author in that row is the PERSON now, not the principal a mount commits as
    expect(say("last")).toContain("Jane Smith");
    expect(say("last")).toContain("2 hours ago");
    expect(say("shared")).toContain("1 member, you owner");
    expect(say("github")).toContain("main, 2 ahead");
    await waitFor(() => expect(say("history")).toContain("1 commit"));
  });

  it("says a read failed rather than rendering a zero", async () => {
    vi.mocked(api.listWorkspaceTree).mockRejectedValue(new Error("boom"));
    const { container } = panel();
    await openDetails(container);

    expect(container.querySelector('[data-ws-disclosure="pages"]')?.textContent).toContain("not readable");
    await screen.findByText("Could not count the pages.");
  });
});

/** The company layer, with its own README (the company's name) and its own POLICIES.md (the
 *  visibility answers and the profile) — and this reader either its administrator or not. */
const asCompanyLayer = (isAdmin: boolean) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true, json: async () => (String(url).includes("/api/auth/me") ? { is_admin: isAdmin } : []),
  })) as unknown as typeof fetch);
  vi.mocked(api.readWorkspaceFile).mockImplementation(async (path: string) =>
    (path === "README.md" ? "# Pilot Industries\n\nWe make the things.\n"
      : "---\nkind: policies\nprofile: default\nglobal_admin_only: on\n---\n\n# Policies\n"));
  return panel({ pages: [{ path: "README.md", slug: "_global", label: "Company" }], docSlug: "_global" });
};

describe("the acts are conversations, and they are this viewer's", () => {
  /** Every act goes out through the one door every act on this screen uses (#1632: *"this add
   *  member should just ask chat to do that with mcp, asking their emails etc."*). */
  const heard = () => {
    const seen: { prompt?: string; display?: string; intent?: unknown }[] = [];
    window.addEventListener(ASK_CHAT_EVENT, (e) => seen.push((e as CustomEvent).detail));
    return seen;
  };

  it("queues a same-target act on the chat instead of opening a form", async () => {
    const seen = heard();
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBe(3));

    fireEvent.click(container.querySelector('[data-ws-strip-act="member"]')!);

    // ADDING A MEMBER IS #1632'S ACT, and the strip is a second DOOR into it rather than a second
    // implementation: the same typed intent, so the server maps the kind to the same ask.
    expect(seen).toHaveLength(1);
    expect(seen[0].intent).toEqual({ kind: "member_add", workspace: "pilot-b5e60c" });
    // …and nothing was minted, invited or written by the press itself
    expect(api.mintInvite).not.toHaveBeenCalled();
    expect(container.querySelector("[data-ws-invite]")).toBeNull();
  });

  it("carries its own instruction for an act that has no verb behind it yet", async () => {
    const seen = heard();
    const { container } = asCompanyLayer(true);
    await waitFor(() => expect(acts(container).length).toBe(3));

    fireEvent.click(container.querySelector('[data-ws-strip-act="editor"]')!);

    // `workspace_invite` REFUSES `_global` (#1632): the company layer's editors are a named set in
    // `POLICIES.md`, so this act asks the chat to write that set — where the answer actually lives.
    expect(seen).toHaveLength(1);
    expect(seen[0].intent).toBeUndefined();
    expect(seen[0].prompt).toContain("_global/POLICIES.md");
    expect(seen[0].prompt).toContain("only write the file if I say yes");
    // the company's own name out of `_global/README.md`, not the slug it is addressed by
    expect(seen[0].display).toBe("Add an editor: Pilot Industries");
  });

  it("gives a READER the one act that is theirs, and no others", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "viewer" }]);
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBe(1));

    expect(acts(container).map((b) => b.getAttribute("data-ws-strip-act"))).toEqual(["history"]);
    // …and it is the icon alone, with its name where a name costs no room (#1634's design spec)
    expect(acts(container)[0].getAttribute("aria-label")).toBe("History");
    expect(acts(container)[0].querySelector("svg")).toBeTruthy();
  });
});

describe("the company layer names its writer, to everybody (Vexa-ai/vexa#1642)", () => {
  const asGlobal = asCompanyLayer;

  it("names the ADMINISTRATOR, resolved from the layer's own history rather than from the reader", async () => {
    const { container } = asGlobal(true);

    await waitFor(() => expect(container.querySelector('[data-ws-line="where"]')?.textContent)
      .toBe("everyone at Pilot Industries reads it, Jane writes it"));
    expect(container.querySelector("[data-ws-eyebrow]")?.textContent).toBe("Company layer");
    expect(container.querySelector("[data-ws-pages]")?.textContent).toBe("2 pages");
    expect(container.querySelector("[data-ws-pill]")?.textContent).toContain("policies: default profile");
    await waitFor(() => expect(acts(container).map((b) => b.getAttribute("data-ws-strip-act")))
      .toEqual(["policies", "editor", "history"]));
  });

  it("names the SAME person to a reader who is not the administrator", async () => {
    // Before #1642 this line said *the admin* to everybody but the administrator — and to the
    // administrator too, whenever his own name failed to resolve, which is what the founder met.
    const { container } = asGlobal(false);

    await waitFor(() => expect(container.querySelector('[data-ws-line="where"]')?.textContent)
      .toBe("everyone at Pilot Industries reads it, Jane writes it"));
    expect(acts(container).map((b) => b.getAttribute("data-ws-strip-act"))).toEqual(["history"]);
  });

  it("drops the clause rather than writing *the admin* when nobody could be resolved", async () => {
    vi.mocked(api.readInstanceAdmin).mockResolvedValue({ name: null, first_name: null });
    const { container } = asGlobal(true);

    await waitFor(() => expect(container.querySelector('[data-ws-line="where"]')?.textContent)
      .toBe("everyone at Pilot Industries reads it"));
  });
});

/** THE ASSERTION THE ISSUE IS NAMED AFTER (Vexa-ai/vexa#1642), snapshot-free and over the whole
 *  rendered header: *"Company layer · everyone at Vexa reads it, **the admin** writes it · 29 pages
 *  / Changed 60 minutes ago by **someone**: five pages"*, on the one instance where both people are
 *  certainly known. Neither word is a thing this header says, in any state it has. */
describe("no rendered word stands in for a person", () => {
  const forbidden = ["someone", "the admin", "@"];
  const saidBy = (c: HTMLElement) => (strip(c)!.textContent ?? "").toLowerCase();

  it("not on a shared workspace, not on the company layer, not with nothing resolved", async () => {
    const shared = panel();
    await waitFor(() => expect(acts(shared.container).length).toBeGreaterThan(0));
    for (const word of forbidden) expect(saidBy(shared.container)).not.toContain(word);
    cleanup();

    const company = asCompanyLayer(true);
    await waitFor(() => expect(acts(company.container).length).toBeGreaterThan(0));
    for (const word of forbidden) expect(saidBy(company.container)).not.toContain(word);
    cleanup();

    // …and with every name unresolved, which is the state that produced both words
    vi.mocked(api.readInstanceAdmin).mockResolvedValue({ name: null, first_name: null });
    vi.mocked(api.readMyPerson).mockResolvedValue({ subject: "126", name: null, first_name: null });
    vi.mocked(api.readLastChange).mockResolvedValue({
      ...CHANGE, change: { ...CHANGE.change, author: null },
    });
    const anon = asCompanyLayer(true);
    await waitFor(() => expect(acts(anon.container).length).toBeGreaterThan(0));
    for (const word of forbidden) expect(saidBy(anon.container)).not.toContain(word);
    // what it says instead names nobody rather than naming a pronoun
    expect(anon.container.querySelector("[data-ws-changed]")?.textContent)
      .toBe("Changed the governing board page 2 hours ago");
  });
});

describe("nothing else is on the strip (#1634 rule 3)", () => {
  it("no commit count, no repo state, no address, no path — only the two sentences and the acts", async () => {
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBeGreaterThan(0));

    const said = strip(container)!.textContent ?? "";
    expect(said).not.toContain("commit");            // the count is behind the disclosure
    expect(said).not.toContain("main, 2 ahead");     // so is the repo state
    expect(said).not.toContain("pilot-b5e60c");       // the address is not a sentence
    expect(said).not.toContain("README.md");         // neither is a path
    expect(said).not.toContain("@");                 // and never an address for a person
  });
});
