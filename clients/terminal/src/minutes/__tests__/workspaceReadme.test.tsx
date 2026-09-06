/** THE WORKSPACE README'S FRONT PAGE (Vexa-ai/vexa#1623).
 *
 *  Founder, 2026-09-06, on a customer workspace's `README.md` in the preview: *"if it's a workspace
 *  readme we want to have data: shared with whom, controls like github sync, git history lookup,
 *  etc."*
 *
 *  Rendered through `PagesPanel`, not as a loose component, because two of the three claims are
 *  about WHERE it appears — and a component tested on its own cannot fail the way the product does:
 *  by showing a workspace's membership above `drafts/plan.md`, or by not showing it at all on the
 *  one page it belongs to.
 *
 *  Three claims, each with a plausible wrong answer:
 *    · it stands on a workspace-root README and on no other page;
 *    · a READER of a workspace sees the data and the history and NOT ONE control — not a disabled
 *      one, not one that explains itself: a control whose only outcome is a 403 teaches a person the
 *      product is broken rather than that they lack the role;
 *    · the history renders as commits a person can read — author, time, message — and the page
 *      filter re-reads scoped to the open page.
 *
 *  SINCE #1628 THE PANEL OPENS AS A STRIP, and since #1634 the strip is TWO SENTENCES with all of
 *  this behind one disclosure — so every one of those claims is now made about a section a reader
 *  has opened: `open()` below presses **History** (the disclosure at the end of line two), which
 *  opens the three sections it carries. That is the distance the founder's "the rest collapsed" and
 *  "a sentence about a place" put between arriving on the page and seeing any of this. The strip's
 *  own claims — the height, the two sentences, the acts, the closed default, the absence of any
 *  summary line under the disclosure — live in `workspaceStrip.test.tsx`; the words themselves in
 *  `frontPageLines.test.ts`.
 *
 *  SINCE #1642's SECOND LOOK there are THREE sections and no summaries of them: **People**, **Repo**,
 *  **History**. The kind, the address, the policy sentence, the page LIST and the last change had
 *  their own summaries and sections here; the first three of those are what the header now says in
 *  two sentences, and the six summaries that opened them were the line the founder rejected.
 *
 *  SINCE #1632 THE THREE MEMBERSHIP CONTROLS POST AN ACT TO THE CHAT rather than calling a route,
 *  so what they do is claimed in `memberActs.test.tsx` and what remains here is where they APPEAR —
 *  the owner sees them, the reader sees none. The arm-then-commit claim moved with them: it was
 *  written on `remove:` because that was the nearest confirmed act, and it is a claim about the
 *  PANEL's own acts, so it is now made on `detach` — which still fires from here and still arms.
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
  readWorkspaceGitDiff: vi.fn(),
  detachWorkspaceRemote: vi.fn(),
}));

import { PagesPanel } from "../PagesPanel";
import { boundSeries, countPages, isWorkspaceReadme, policySentence, workspaceMeetings } from "../workspaceReadme";
import type { Page } from "../types";

/** The three sentences the panel lifts out of the seeded `behavior/global/POLICIES.md`, in the
 *  shapes that file actually uses (a bullet under *What is not yours to choose*, and two rule
 *  headings). Trimmed to what the parser reads; the real file is ~350 lines of the same shapes. */
const POLICIES = `---
kind: policies
global_admin_only: on
---

## What is not yours to choose

- a **participant** reads the transcript and the report of a meeting they were in;
- a **member** reads a group; an **owner or contributor** writes it;
- **\`_system\` is read by no agent for anybody else** — chats, sessions and settings are the one
  genuinely private tier, and no rule below can widen it;

<a id="agent_reads_desk"></a>
### \`agent_reads_desk\` — an agent may read its user's desk when its user is a participant

**Default \`on\`.**

### \`global_admin_only\` — only the admin writes \`_global\` (editors may be added)
`;

const COMMITS = [
  { sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", author: "126", kind: "you" as const, files: ["README.md"] },
  { sha: "00eb951", msg: "entity acme", when: "yesterday", author: "Jane Smith", kind: "member" as const, files: ["kg/entities/acme.md"] },
];

const READ_ME: Page[] = [{ path: "README.md", slug: "pilot-b5e60c", label: "Pilot" }];

const panel = (over: Partial<Parameters<typeof PagesPanel>[0]> = {}) =>
  render(<PagesPanel pages={READ_ME} docPath="README.md" docSlug="pilot-b5e60c" onOpen={() => {}}
    body={"# Pilot\n\nThe workspace body."} {...over} />);

/** Wait for the strip, press **History** — the one disclosure #1634 left at the end of line two —
 *  and wait for the section the claim is about. Nothing below it exists before the click, and since
 *  #1642 all three sections arrive together: the helper means "have this section on the page". */
const open = async (container: HTMLElement, id: "people" | "repo" | "history") => {
  const details = await waitFor(() => {
    const d = container.querySelector<HTMLButtonElement>("[data-ws-details]");
    if (!d) throw new Error("the strip has not answered yet");
    return d;
  });
  if (details.getAttribute("aria-expanded") !== "true") fireEvent.click(details);
  return waitFor(() => {
    const s = container.querySelector<HTMLElement>(`[data-ws-section="${id}"]`);
    if (!s) throw new Error(`no "${id}" section behind the details yet`);
    return s;
  });
};

/** `/api/auth/me` (is this person the admin) and `/api/meetings` (what is bound here) are the two
 *  reads the panel makes outside `workspaceApi`. */
const serveFetch = (meetings: unknown[] = [], isAdmin = false) => {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => ({
    ok: true,
    json: async () => (String(url).includes("/api/auth/me") ? { is_admin: isAdmin } : meetings),
  })) as unknown as typeof fetch);
};

beforeEach(() => {
  window.localStorage.clear();     // the remembered open section is per browser — and per test
  vi.mocked(api.readWorkspaceBySlug).mockResolvedValue({ id: "w1", name: "Pilot", kind: "group", slug: "pilot-b5e60c", access: "readable", writable: false });
  vi.mocked(api.listWorkspaceTree).mockResolvedValue(["README.md", "kg/entities/acme.md", "flows/post.md", ".git/config"]);
  vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "pilot-b5e60c", branch: "main", path: null, limit: 20, commits: COMMITS });
  vi.mocked(api.readLastChange).mockResolvedValue({
    slug: "pilot-b5e60c", path: null,
    change: { sha: "7f6b769", msg: "readme: link the entity", when: "2 hours ago", ts: 0,
              kind: "you", author: null, count: 1, files: ["README.md"],
              pages: [{ path: "README.md", title: "the front page" }] },
  });
  vi.mocked(api.readMyPerson).mockResolvedValue({ subject: "126", name: null, first_name: null });
  vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: true, remote: "origin", url: "https://github.com/pilot/kg", branch: "main", tracked: true, ahead: 2, behind: 0 });
  vi.mocked(api.readWorkspaceFile).mockResolvedValue(POLICIES);
  vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "viewer" }]);
  vi.mocked(api.listWorkspaceMembers).mockRejectedValue(new Error("403"));
  serveFetch();
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); window.localStorage.clear(); });

// ── pure rules ───────────────────────────────────────────────────────────────────────────────────
describe("which page is a workspace's front page", () => {
  it("the README at the ROOT is, and a README anywhere else is not", () => {
    expect(isWorkspaceReadme("README.md")).toBe(true);
    expect(isWorkspaceReadme("readme.md")).toBe(true);          // git is case-sensitive; people are not
    expect(isWorkspaceReadme("drafts/README.md")).toBe(false);  // a page about drafts
    expect(isWorkspaceReadme("kg/entities/acme.md")).toBe(false);
  });
});

describe("the policy sentence is READ from _global/POLICIES.md, never retyped", () => {
  it("one sentence per kind, in the file's own words", () => {
    expect(policySentence("group", POLICIES)).toBe("a member reads a group; an owner or contributor writes it");
    expect(policySentence("desk", POLICIES)).toBe("an agent may read its user's desk when its user is a participant");
    expect(policySentence("global", POLICIES)).toBe("only the admin writes _global (editors may be added)");
  });
  it("says nothing rather than something stale when the file cannot be read or has moved on", () => {
    expect(policySentence("group", null)).toBeNull();
    expect(policySentence("group", "# Policies\n\nrewritten past recognition\n")).toBeNull();
  });
});

describe("the counts and the bindings", () => {
  it("counts the pages a reader is shown — not machinery, not dotfiles, not non-pages", () => {
    expect(countPages(["README.md", "kg/entities/acme.md", "flows/post.md", ".git/config", "assets/logo.svg"])).toBe(2);
  });
  it("collapses the runs of one recurring invite into one thing the workspace is bound to", () => {
    const rows = [
      { id: 1, data: { workspace_id: "w", calendar_uid: "u1", title: "Tuesday sync" }, start_time: "2026-09-01" },
      { id: 2, data: { workspace_id: "w", calendar_uid: "u1", title: "Tuesday sync" }, start_time: "2026-09-08" },
      { id: 3, data: { workspace_id: "w", title: "One-off review" }, start_time: "2026-08-30" },
      { id: 4, data: { workspace_id: "other", calendar_uid: "u9", title: "Somebody else's" } },
    ];
    expect(boundSeries(rows, "w")).toEqual([
      { key: "cal:u1", title: "Tuesday sync", recurring: true, runs: 2, latest: "2026-09-08" },
      { key: "row:3", title: "One-off review", recurring: false, runs: 1, latest: "2026-08-30" },
    ]);
  });
});

// A bot requested inside a workspace makes the WORKSPACE's meeting (Vexa-ai/vexa#1648), so the front
// page lists the group's calls rather than the reader's own — and says which one is on RIGHT NOW.
describe("the workspace's own meetings", () => {
  const ROWS = [
    { id: 1, status: "completed", data: { workspace_id: "w", title: "Last week's review" }, start_time: "2026-09-01" },
    { id: 2, status: "active", shared: true, data: { workspace_id: "w", title: "Group call" }, start_time: "2026-09-06" },
    { id: 3, status: "scheduled", data: { workspace_id: "w", title: "Next Tuesday" }, start_time: "2026-09-30" },
    { id: 4, status: "active", data: { workspace_id: "other", title: "Another group's" }, start_time: "2026-09-06" },
  ];

  it("lists only this workspace's meetings, live ones first", () => {
    // #3 is SCHEDULED for the 30th — the furthest-future start_time in the set. It must still sort
    // BELOW the live call, which is the whole reason the live lift is its own sort key and not a
    // time comparison: on a pure time sort the meeting that has not happened wins the top row.
    expect(workspaceMeetings(ROWS, "w").map((m) => [m.title, m.live])).toEqual([
      ["Group call", true],
      ["Next Tuesday", false],
      ["Last week's review", false],
    ]);
  });

  it("marks a colleague's meeting as reaching this reader through the workspace", () => {
    const [live] = workspaceMeetings(ROWS, "w");
    expect(live).toMatchObject({ id: "2", shared: true, when: "2026-09-06" });
  });

  it("treats every phase of the bot arriving and leaving as live", () => {
    for (const status of ["requested", "joining", "awaiting_admission", "active", "stopping"]) {
      const rows = [{ id: 9, status, data: { workspace_id: "w", title: "x" } }];
      expect(workspaceMeetings(rows, "w")[0].live).toBe(true);
    }
    for (const status of ["completed", "failed", "idle", "scheduled"]) {
      const rows = [{ id: 9, status, data: { workspace_id: "w", title: "x" } }];
      expect(workspaceMeetings(rows, "w")[0].live).toBe(false);
    }
  });

  it("answers with nothing when no slug is named, rather than everything", () => {
    expect(workspaceMeetings(ROWS, "")).toEqual([]);
  });
});

// ── where it appears ─────────────────────────────────────────────────────────────────────────────
describe("the panel stands on a workspace README and nowhere else", () => {
  it("renders between the slug line and the body of a workspace-root README", async () => {
    const { container } = panel();
    const where = await waitFor(() => {
      const w = container.querySelector('[data-ws-line="where"]');
      if (!w?.textContent) throw new Error("the first line has not answered yet");
      return w;
    });
    // The KIND is the eyebrow since #1642 applied #1634's design spec — it is a label above the
    // title, not a clause in the middle of a sentence about people.
    expect(container.querySelector("[data-ws-eyebrow]")?.textContent).toBe("Shared workspace");
    expect(where.textContent).toContain("you");
    // between the crumb above and the prose below — the reading order is the claim
    const text = container.textContent ?? "";
    expect(text.indexOf("Shared workspace")).toBeLessThan(text.indexOf("The workspace body."));
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("group");
  });

  it("takes the README's own first heading as the title, and the body does not repeat it", async () => {
    const { container } = panel();
    await waitFor(() => expect(container.querySelector("[data-ws-title]")).toBeTruthy());

    expect(container.querySelector("[data-ws-title]")?.textContent).toBe("Pilot");
    // …and exactly once as a heading: a title printed twice two lines apart reads as a bug
    expect([...container.querySelectorAll("h1")].map((h) => h.textContent)).toEqual(["Pilot"]);
    expect(container.textContent).toContain("The workspace body.");
  });

  it("does NOT render on an ordinary page", async () => {
    const { container } = panel({
      pages: [{ path: "kg/entities/acme.md", slug: "pilot-b5e60c", label: "acme" }],
      docPath: "kg/entities/acme.md", body: "# Acme\n\nA company.",
    });
    await screen.findByText("A company.");
    expect(container.querySelector("[data-ws-readme]")).toBeNull();
    expect(api.readWorkspaceHistory).not.toHaveBeenCalled();
    expect(api.readLastChange).not.toHaveBeenCalled();
  });

  it("does not render while the README is being EDITED — an editor edits a file", async () => {
    const { container } = panel();
    await waitFor(() => expect(container.querySelector("[data-ws-strip]")).toBeTruthy());
    fireEvent.click(container.querySelector('[data-doc-act="edit"]')!);
    expect(container.querySelector("[data-ws-readme]")).toBeNull();
  });
});

// ── the data ─────────────────────────────────────────────────────────────────────────────────────
describe("the data a workspace README carries", () => {
  it("says what it is, how big it is and who is here — in the header, before anything is opened", async () => {
    const { container } = panel();
    await waitFor(() => expect(container.querySelector("[data-ws-eyebrow]")?.textContent).toBe("Shared workspace"));

    // WHAT IT IS is the eyebrow, HOW BIG is the people row's count, WHO IS HERE is its sentence, and
    // WHAT LAST HAPPENED is line two. All four without a click — and the ADDRESS is not among them:
    // `pilot-b5e60c` is how the panel reaches this workspace, not something a person needs told.
    expect(container.querySelector("[data-ws-pages]")?.textContent).toBe("2 pages");
    expect(container.querySelector('[data-ws-line="where"]')?.textContent).toContain("you");
    expect(container.querySelector("[data-ws-changed]")?.textContent).toContain("the front page");
    expect(container.querySelector("[data-ws-readme]")?.textContent).not.toContain("pilot-b5e60c");
  });

  it("names what it could not read instead of rendering a zero", async () => {
    vi.mocked(api.listWorkspaceTree).mockRejectedValue(new Error("boom"));
    const { container } = panel();
    await waitFor(() => expect(container.querySelector("[data-ws-strip]")).toBeTruthy());
    // the count is absent rather than `0 pages`, and the failure is named at the foot
    await waitFor(() => expect(container.querySelector("[data-ws-pages]")).toBeNull());
    expect([...container.querySelectorAll("[data-ws-note]")].map((n) => n.textContent))
      .toContain("Could not count the pages.");
  });
});

// ── who may act ──────────────────────────────────────────────────────────────────────────────────
describe("a reader sees data and history, and no controls", () => {
  it("renders not one control for a viewer of the workspace", async () => {
    const { container } = panel();
    await open(container, "people");
    await screen.findByText(/You are a reader here/);
    // ALL THREE SECTIONS ARE ON THE PAGE AT ONCE since #1642, so this is the whole claim in one
    // assertion rather than three: not one control anywhere behind the disclosure.
    expect(container.querySelectorAll("[data-ws-act]")).toHaveLength(0);
    // …and the data is all there, which is the other half of the claim
    expect(container.querySelector('[data-ws-fact="remote"]')).toBeTruthy();
    expect(container.querySelector("[data-ws-history]")).toBeTruthy();
  });

  it("gives the OWNER the membership and the GitHub controls", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "owner" }]);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([
      { subject: "126", role: "owner", email: "jsmith@example.com" },
      { subject: "77", role: "viewer", email: "jdoe@example.com" },
    ]);
    const { container } = panel();
    const people = await open(container, "people");
    await screen.findByText("jsmith@example.com");
    const shared = [...people.querySelectorAll("[data-ws-act]")].map((b) => b.getAttribute("data-ws-act"));
    expect(shared).toEqual(expect.arrayContaining(["member-add", "member-remove:77", "member-role:77"]));
    // the OWNER's own row carries no remove/role control — a workspace with no owner is not a state
    expect(shared).not.toContain("member-remove:126");

    const repo = await open(container, "repo");
    const git = [...repo.querySelectorAll("[data-ws-act]")].map((b) => b.getAttribute("data-ws-act"));
    expect(git).toEqual(expect.arrayContaining(["sync", "pull", "push", "detach"]));
  });

  // RULE 2 IS STILL RULE 2 — for the acts this panel still fires itself (#1632). It was claimed on
  // `remove:` while removal was one of them; `detach` is now the destructive one that stays here,
  // and it is the same claim on the same `Act` component.
  it("an owner-only act ARMS before it fires, and says what it will do", async () => {
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "owner" }]);
    vi.mocked(api.detachWorkspaceRemote).mockResolvedValue({ detached: true, remote: "origin", url: "https://github.com/pilot/kg" });
    const { container } = panel();
    await open(container, "repo");
    await screen.findByText("https://github.com/pilot/kg");

    fireEvent.click(container.querySelector('[data-ws-act="detach"]')!);

    // the armed state SAYS what will happen, and the first click has done nothing
    expect(screen.getByText("Stop syncing to GitHub — the files here stay exactly as they are")).toBeTruthy();
    expect(api.detachWorkspaceRemote).not.toHaveBeenCalled();

    fireEvent.click(container.querySelector('[data-ws-act-cancel="detach"]')!);

    expect(container.querySelector('[data-ws-confirm="detach"]')).toBeNull();
    expect(api.detachWorkspaceRemote).not.toHaveBeenCalled();

    // …and the second click is the one that acts
    fireEvent.click(container.querySelector('[data-ws-act="detach"]')!);
    fireEvent.click(container.querySelector('[data-ws-act-confirm="detach"]')!);
    await waitFor(() => expect(api.detachWorkspaceRemote).toHaveBeenCalled());
  });
});

// ── the history ──────────────────────────────────────────────────────────────────────────────────
describe("git history lookup", () => {
  it("lists the workspace's commits with who, when and what — and the files each one touched", async () => {
    const { container } = panel();
    await open(container, "history");
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));
    const rows = [...container.querySelectorAll("[data-ws-commit]")].map((r) => r.textContent ?? "");
    expect(rows[0]).toContain("7f6b769");
    expect(rows[0]).toContain("readme: link the entity");
    expect(rows[0]).toContain("126");
    expect(rows[1]).toContain("Jane Smith");
    // THE FILES ARE THE CHECK ON THE MESSAGE. A turn-commit's message names the file the turn was
    // about while the commit touches several — which is how a correctly filtered list read as an
    // unfiltered one on `_global` (Vexa-ai/vexa#1628).
    expect(rows[1]).toContain("kg/entities/acme.md");
  });

  it("the page filter re-reads the history scoped to the open page, and SAYS which scope is showing", async () => {
    const { container } = panel();
    await open(container, "history");
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));
    // the unfiltered list does not claim to be filtered — the founder's screenshot was this state
    expect(container.querySelector("[data-ws-history-scope]")?.textContent).toBe("every commit in this workspace");
    expect(container.querySelector("[data-ws-history-filter]")?.getAttribute("aria-pressed")).toBe("false");
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({
      slug: "pilot-b5e60c", branch: "main", path: "README.md", limit: 11, commits: [COMMITS[0]],
    });

    fireEvent.click(container.querySelector("[data-ws-history-filter]")!);

    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(1));
    expect(api.readWorkspaceHistory).toHaveBeenLastCalledWith("pilot-b5e60c", { path: "README.md", limit: 11 });
    expect(container.querySelector("[data-ws-history-scope]")?.textContent).toBe("only commits touching README.md");
    expect(container.querySelector("[data-ws-history-filter]")?.getAttribute("aria-pressed")).toBe("true");
  });

  it("shows ten and a MORE link — in the whole workspace and on one page alike", async () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => ({ ...COMMITS[0], sha: `c${i}`, msg: `commit ${i}` }));
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "pilot-b5e60c", branch: "main", path: null, limit: 11, commits: many(11) });
    const { container } = panel();
    await open(container, "history");

    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(10));
    expect(container.querySelector("[data-ws-history-more]")).toBeTruthy();
    // the eleventh was never rendered — it is the answer to "is there an eleventh"
    expect(container.textContent).not.toContain("commit 10");

    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "pilot-b5e60c", branch: "main", path: null, limit: 21, commits: many(15) });
    fireEvent.click(container.querySelector("[data-ws-history-more]")!);

    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(15));
    expect(api.readWorkspaceHistory).toHaveBeenLastCalledWith("pilot-b5e60c", { path: undefined, limit: 21 });
    expect(container.querySelector("[data-ws-history-more]")).toBeNull();     // nothing left to ask for
  });

  it("clicking a commit shows its diff, read-only", async () => {
    vi.mocked(api.readWorkspaceGitDiff).mockResolvedValue({ sha: "7f6b769", diff: "@@ -1 +1 @@\n-# Pilot\n+# Pilot\n+links" });
    const { container } = panel();
    await open(container, "history");
    await waitFor(() => expect(container.querySelectorAll("[data-ws-commit]").length).toBe(2));

    fireEvent.click(container.querySelector('[data-ws-commit="7f6b769"] button')!);

    await waitFor(() => expect(container.querySelector("[data-ws-diff]")?.textContent).toContain("+links"));
    expect(container.querySelector("[data-ws-diff]")!.tagName).toBe("PRE");   // shown, never editable
  });
});

// ── no repo attached is a STATE, not a failure ────────────────────────────────────────────────────
describe("the GitHub section tells a missing repo from a broken read", () => {
  it("says NO REPO ATTACHED, with the existing attach flow for an owner and no red line", async () => {
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: null, tracked: false, ahead: 0, behind: 0 });
    vi.mocked(api.listSharedMemberships).mockResolvedValue([{ workspace_id: "pilot-b5e60c", role: "owner" }]);
    vi.mocked(api.listWorkspaceMembers).mockResolvedValue([{ subject: "126", role: "owner", email: "jsmith@example.com" }]);
    const { container } = panel();
    await open(container, "repo");

    expect(container.querySelector('[data-ws-github="unattached"]')?.textContent).toContain("No repo attached");
    expect(container.querySelector('[data-ws-act="attach"]')).toBeTruthy();
    // nothing red, anywhere: an ordinary state must not spend the colour that means "this is broken"
    expect(container.querySelector("[data-ws-github-failed]")).toBeNull();
    expect(container.querySelectorAll("[data-ws-note]")).toHaveLength(0);
    // …and the state is said HERE and nowhere else: `no repo attached` was also one of #1628's six
    // summaries, which is the line the founder rejected (Vexa-ai/vexa#1642).
    expect(container.querySelector("[data-ws-sections]")).toBeNull();
  });

  it("offers a READER no attach control — it would only ever produce a refusal", async () => {
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: null, tracked: false, ahead: 0, behind: 0 });
    const { container } = panel();       // the default membership in this file is `viewer`
    await open(container, "repo");

    await screen.findByText("No repo attached.");
    expect(container.querySelector('[data-ws-act="attach"]')).toBeNull();
  });

  it("keeps the red line for a read that actually failed, and NAMES what failed", async () => {
    vi.mocked(api.gitRemoteStatus).mockRejectedValue(new Error("upstream unreachable: ConnectError"));
    const { container } = panel();
    await open(container, "repo");

    const said = container.querySelector("[data-ws-github-failed]")?.textContent ?? "";
    expect(said).toContain("Could not read the GitHub state");
    expect(said).toContain("upstream unreachable: ConnectError");
    expect(container.querySelector('[data-ws-github="unattached"]')).toBeNull();
  });
});

// ── the desk and the company layer ───────────────────────────────────────────────────────────────
describe("the two workspaces that are not groups", () => {
  it("a DESK says who writes it and that the company's agents read it", async () => {
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "personal", branch: "main", path: null, limit: 11, commits: COMMITS });
    const { container } = panel({
      pages: [{ path: "README.md", label: "Desk", desk: true }], docSlug: undefined,
    });
    await open(container, "people");
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("desk");
    expect(container.querySelector('[data-ws-members="desk"]')?.textContent).toContain("agents read it");
    // …and the rule it comes from is not ALSO printed as a fact row: `Policy — an agent may read
    // its user's desk when its user is a participant` was a rule in a table, which is what the
    // header's own sentence replaced (Vexa-ai/vexa#1634, and #1642's second look).
    expect(container.querySelector('[data-ws-fact="policy"]')).toBeNull();
    // the desk tab carries no slug, so both reads are addressed by the name the server keeps for it
    expect(api.readLastChange).toHaveBeenCalledWith("personal");
    expect(api.readWorkspaceHistory).toHaveBeenCalledWith("personal", { path: undefined, limit: 11 });
  });

  it("the COMPANY LAYER says everybody reads and the admin writes — and offers a non-admin nothing", async () => {
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "_global", branch: "main", path: null, limit: 11, commits: COMMITS });
    const { container } = panel({
      pages: [{ path: "README.md", slug: "_global", label: "Company" }], docSlug: "_global",
    });
    await open(container, "people");
    expect(container.querySelector("[data-ws-readme]")?.getAttribute("data-ws-kind")).toBe("global");
    expect(container.querySelector('[data-ws-members="global"]')?.textContent).toContain("administrator writes it");
    expect(container.querySelectorAll("[data-ws-act]")).toHaveLength(0);
  });

  it("the COMPANY LAYER with no remote reads NO REPO ATTACHED for the admin, not an error", async () => {
    // The exact screen the founder was looking at: `_global`, signed in as the administrator.
    vi.mocked(api.readWorkspaceHistory).mockResolvedValue({ slug: "_global", branch: "main", path: null, limit: 11, commits: COMMITS });
    vi.mocked(api.gitRemoteStatus).mockResolvedValue({ has_home: false, remote: null, url: null, branch: null, tracked: false, ahead: 0, behind: 0 });
    serveFetch([], true);
    const { container } = panel({
      pages: [{ path: "README.md", slug: "_global", label: "Company" }], docSlug: "_global",
    });
    await open(container, "repo");

    expect(container.querySelector('[data-ws-github="unattached"]')?.textContent).toContain("No repo attached");
    expect(container.querySelector("[data-ws-github-failed]")).toBeNull();
    expect(container.querySelectorAll("[data-ws-note]")).toHaveLength(0);
    // …and no attach button, because the company layer is not one of that flow's targets: a control
    // whose only outcome is a refusal is the thing this panel refuses to render.
    expect(container.querySelector('[data-ws-act="attach"]')).toBeNull();
    await screen.findByText(/mounted read-only into every worker/);
  });
});
