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
 *    · THE DISCLOSURE IS CLOSED ON ARRIVAL WHATEVER IS IN STORAGE (Vexa-ai/vexa#1642, second look).
 *      The founder's browser opened `_global` with the details ALREADY OPEN, because #1628's
 *      remembered posture (`vexa.wsreadme.open:<slug>`) was read on mount and a build three shas
 *      earlier had written `history` into it. The key is retired, nothing is read, and a value left
 *      by any earlier build is inert.
 *    · WHAT IS BEHIND IT IS THREE SECTIONS AND NO SUMMARY OF THEM. *Company layer · 30 pages ·
 *      \_global: .claude/mcp.json… · you · 49 minutes ago · Everyone reads, the admin writes · no
 *      repo attached · 10+ commits* was the first block under the disclosure — the exact line the
 *      founder rejected on the front page, one level down. The sections are People, Repo, History.
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
const details = (c: HTMLElement) => c.querySelector("[data-ws-details-region]") as HTMLElement | null;
const openIds = (c: HTMLElement) => [...c.querySelectorAll("[data-ws-section]")].map((s) => s.getAttribute("data-ws-section"));
const acts = (c: HTMLElement) => [...c.querySelectorAll<HTMLButtonElement>("[data-ws-strip-act]")];

/** THE ONE DISCLOSURE (#1634 rule 6) — History, at the end of line two. The three sections are
 *  behind it, so every test about a section starts by pressing it. */
const openDetails = async (c: HTMLElement) => {
  await waitFor(() => expect(c.querySelector("[data-ws-details]")).toBeTruthy());
  fireEvent.click(c.querySelector("[data-ws-details]")!);
  await waitFor(() => expect(details(c)).toBeTruthy());
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
  it("caps the two ROWS against the viewport, and the cap is inside the founder's 1/8", async () => {
    // A viewport this test states rather than inherits: the claim is a fraction of the screen, so a
    // jsdom default of 768 would be measuring a coincidence.
    Object.defineProperty(window, "innerHeight", { value: 900, configurable: true });

    const { container } = panel();
    await waitFor(() => expect(strip(container)).toBeTruthy());

    // WHAT IS MEASURED is the furniture — the people row and the last-change row — and NOT the
    // eyebrow and title above them (#1642). The title is the README's own first heading, which was
    // already on the page as the body's `# `; counting a line the reader already had against a cap
    // on what this panel ADDS shrank the rows to pay for it, and on the founder's own 384px panel
    // the last-change sentence ended up clipped out of reach.
    const rows = container.querySelector("[data-ws-rows]") as HTMLElement;
    expect(rows).toBeTruthy();
    expect(rows.style.maxHeight).toBe(`${STRIP_MAX_VH}vh`);   // the cap is in the style, not in luck
    expect(rows.style.overflowY).toBe("auto");                // …and nothing is clipped out of reach
    expect(strip(container)!.style.maxHeight).toBe("");       // the head of the page is not capped
    // …and the cap the style names is inside "1/8 screen at max" on the viewport above
    expect((STRIP_MAX_VH / 100) * window.innerHeight).toBeLessThanOrEqual(window.innerHeight / 8);
  });

  it("renders NOTHING but the two sentences and the acts until the disclosure is opened", async () => {
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBeGreaterThan(0));

    expect(openIds(container)).toEqual([]);
    // not hidden — absent: the whole details region, and with it every section
    expect(details(container)).toBeNull();
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

  it("opens the three sections at once — the reader asked for the history, not for a menu", async () => {
    const { container } = panel();
    await openDetails(container);

    // ALL THREE, in reading order, and no fourth: the kind, the page count and the last change are
    // said once each in the header above, and saying them again here is the strip returning.
    expect(openIds(container)).toEqual(["people", "repo", "history"]);
    expect([...container.querySelectorAll("[data-ws-section-name]")].map((h) => h.textContent))
      .toEqual(["People", "Repo", "History"]);
    await waitFor(() => expect(container.querySelector('[data-ws-fact="remote"]')).toBeTruthy());
    expect(container.querySelector("[data-ws-member]")).toBeTruthy();
    expect(container.querySelector("[data-ws-history]")).toBeTruthy();
    expect(container.querySelector("[data-ws-history-filter]")).toBeTruthy();   // with its scope toggle
  });

  it("puts the whole of #1628's panel away again when History is pressed a second time", async () => {
    const { container } = panel();
    await openDetails(container);

    fireEvent.click(container.querySelector("[data-ws-details]")!);

    expect(details(container)).toBeNull();
    expect(openIds(container)).toEqual([]);
    expect(container.querySelector("[data-ws-details]")!.getAttribute("aria-expanded")).toBe("false");
  });
});

/** THE ASSERTION THE SECOND LOOK IS ABOUT (Vexa-ai/vexa#1642): *"its first block is the OLD strip
 *  line … the exact text the founder rejected, now one level down."* */
describe("the details body carries its sections and never a summary of them", () => {
  const forbidden = ["no repo attached", "10+ commits", "everyone reads"];

  it("opens on the People section, with no summary line above it and none in it", async () => {
    const { container } = panel();
    await openDetails(container);

    // the FIRST thing under the disclosure is the first section itself
    expect(details(container)!.firstElementChild?.getAttribute("data-ws-section")).toBe("people");
    // …and #1628's summary row is not in the document in any form
    expect(container.querySelector("[data-ws-sections]")).toBeNull();
    expect(container.querySelectorAll("[data-ws-disclosure]")).toHaveLength(0);

    const said = (details(container)!.textContent ?? "").toLowerCase();
    for (const word of forbidden) expect(said).not.toContain(word);
    // nor the header's own answers, repeated one level down. (The commit MESSAGE is not among them:
    // it is the history list's own content, which is what a reader pressed History for.)
    expect(said).not.toContain("shared workspace");           // the kind is the eyebrow
    expect(said).not.toContain("2 pages");                    // the count is in the people row
    // …while the header still says all three, so this is a claim about WHERE, not about whether
    const head = strip(container)!.textContent ?? "";
    expect(head).toContain("Shared workspace");
    expect(head).toContain("2 pages");
  });

  it("says nothing summary-shaped on the company layer with no repo either — the founder's own screen", async () => {
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: null, tracked: false, ahead: 0, behind: 0 });
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({
      slug: "_global", branch: "main", path: null, limit: 11,
      commits: Array.from({ length: 11 }, (_, i) => ({ ...COMMITS[0], sha: `c${i}`, msg: `commit ${i}` })),
    });
    const { container } = asCompanyLayer(true);
    await openDetails(container);

    const said = details(container)!.textContent ?? "";
    // eleven commits, and nowhere the words `10+ commits`; a missing remote, and nowhere the words
    // `no repo attached` as a summary — the Repo SECTION says `No repo attached.` and that is the
    // section answering for itself, which is the whole difference this issue is about.
    expect(said).not.toContain("10+ commits");
    expect(said).not.toContain("Everyone reads, the admin writes");
    expect(container.querySelector("[data-ws-sections]")).toBeNull();
    expect(container.querySelectorAll("[data-ws-disclosure]")).toHaveLength(0);
    expect(details(container)!.firstElementChild?.getAttribute("data-ws-section")).toBe("people");
    expect(container.querySelector('[data-ws-github="unattached"]')?.textContent).toContain("No repo attached");
  });
});

describe("the disclosure is closed on arrival, whatever an earlier build remembered", () => {
  /** THE FOUNDER'S BROWSER, RECONSTRUCTED: `vexa.wsreadme.open:<slug>` written by the build that
   *  shipped #1628's summaries, and read on mount by the one that shipped #1634's disclosure. */
  const stale = (slug: string, value: string) => window.localStorage.setItem(`vexa.wsreadme.open:${slug}`, value);

  it("ignores a stored `open` posture — the panel reads no storage at all", async () => {
    stale("pilot-b5e60c", "history");
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBeGreaterThan(0));

    expect(container.querySelector("[data-ws-details]")!.getAttribute("aria-expanded")).toBe("false");
    expect(details(container)).toBeNull();
    expect(openIds(container)).toEqual([]);
    expect(container.querySelector("[data-ws-history]")).toBeNull();
    // …and the stale value is left exactly as it was: not read, not migrated, not rewritten
    expect(window.localStorage.getItem("vexa.wsreadme.open:pilot-b5e60c")).toBe("history");
  });

  it("stores nothing when the reader opens it, so the next visit opens closed again", async () => {
    const { container } = panel();
    await openDetails(container);
    expect(window.localStorage.length).toBe(0);           // no key, versioned or otherwise
    cleanup();

    const again = panel();
    await waitFor(() => expect(again.container.querySelector("[data-ws-details]")).toBeTruthy());
    expect(details(again.container)).toBeNull();
    expect(again.container.querySelector("[data-ws-details]")!.getAttribute("aria-expanded")).toBe("false");

    // the server was asked nothing either: every call this panel made was a READ of the workspace
    const posted = (globalThis.fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .filter((c) => (c[1] as RequestInit | undefined)?.method && (c[1] as RequestInit).method !== "GET");
    expect(posted).toEqual([]);
  });
});

describe("what could not be read is still said", () => {
  it("names the failed read rather than rendering a zero", async () => {
    vi.mocked(api.listWorkspaceTree).mockRejectedValue(new Error("boom"));
    const { container } = panel();
    await waitFor(() => expect(acts(container).length).toBeGreaterThan(0));

    // the count is simply absent from the people row — never a `0 pages` that reads as an answer
    expect(container.querySelector("[data-ws-pages]")).toBeNull();
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

describe("the title never repeats the eyebrow", () => {
  it("a desk whose README opens `# Your desk` shows the eyebrow once and no title", async () => {
    // Seen in a browser on the seeded desk: `Your desk` on one line and `Your desk` again in 19px
    // under it. A title that repeats the label above it is not a title.
    vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "Desk", kind: "desk", slug: "personal", access: "readable", writable: true });
    const { container } = panel({
      pages: [{ path: "README.md", slug: undefined, label: "Desk" }], docSlug: undefined,
      body: "# Your desk\n\nWhat you are working on.",
    });

    await waitFor(() => expect(container.querySelector("[data-ws-eyebrow]")?.textContent).toBe("Your desk"));
    expect(container.querySelector("[data-ws-title]")).toBeNull();
    // …and the body does not get it back either — it was lifted, not hidden
    expect([...container.querySelectorAll("h1")].map((h) => h.textContent)).toEqual([]);
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
