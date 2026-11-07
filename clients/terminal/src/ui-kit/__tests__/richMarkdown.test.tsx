/** Richer pages, same markdown (#1783; founder: "we need richer formatting and interactive and
 *  navigation links"). */
import { describe, it, expect, vi, afterEach } from "vitest";
import { within, cleanup, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { enrichMarkdown, toggleTaskInFile, relativeDay } from "../richMarkdown";
import { MdxDoc, DocMetaContext } from "../MdxDoc";
import { registerTab } from "../../contributions";
import { ASK_CHAT_EVENT } from "../../platform/events";

const api = vi.hoisted(() => ({ file: "", writes: [] as string[] }));
vi.mock("../../data/workspaceApi", async (orig) => ({
  ...(await orig<typeof import("../../data/workspaceApi")>()),
  readWorkspaceFile: vi.fn(async () => api.file),
  writeWorkspaceFile: vi.fn(async (_p: string, c: string) => { api.writes.push(c); return { path: _p, written: true }; }),
  listWorkspaceTree: vi.fn(async () => []),
}));

afterEach(() => { cleanup(); api.writes = []; });

const PAGE = `---
type: person
role: Head of Platform
company: Acme Bank
email: pat@example.test
last_contact: 2026-10-10
---
# Pat Mason

> [!warning] Renewal risk
> Budget is frozen until Q1.

## Notes
- [ ] Send the pricing deck
- [x] Intro call

## Follow-ups
- TBD: share the SOC 2 report
TBD: book a demo with their CISO

\`\`\`
- [ ] not a task, it is code
\`\`\`
`;

describe("reading more of the markdown", () => {
  it("tasks, TBD follow-ups, callouts and headings; code is left alone", () => {
    const r = enrichMarkdown(PAGE.split("---\n")[2]);
    expect(r.tasks.map((t) => [t.kind, t.text, t.done])).toEqual([
      ["box", "Send the pricing deck", false], ["box", "Intro call", true],
      ["tbd", "share the SOC 2 report", false], ["tbd", "book a demo with their CISO", false]]);
    expect(r.src).toContain('<Warning title="Renewal risk">');
    expect(r.src).toContain("- [ ] not a task, it is code");
    expect(r.headings.map((h) => h.text)).toEqual(["Pat Mason", "Notes", "Follow-ups"]);
  });
  it("a TBD line outside a follow-ups section stays prose", () => {
    expect(enrichMarkdown("## Notes\nTBD: maybe").tasks).toEqual([]);
  });
  it("ticking writes the line back: a box flips, a TBD becomes a done task", () => {
    const r = enrichMarkdown(PAGE);
    expect(toggleTaskInFile(PAGE, r.tasks[0], true)).toContain("- [x] Send the pricing deck");
    expect(toggleTaskInFile(PAGE, r.tasks[2], true)).toContain("- [x] share the SOC 2 report");
    expect(toggleTaskInFile("changed", r.tasks[0], true)).toBeNull();
  });
  it("dates read relatively", () => {
    const now = Date.parse("2026-10-13T12:00:00");
    expect(relativeDay("2026-10-13", now)).toBe("today");
    expect(relativeDay("2026-10-10", now)).toBe("3 days ago");
    expect(relativeDay("2026-10-27", now)).toBe("in 2 weeks");
  });
});

describe("the page", () => {
  const renderPage = () => render(
    <DocMetaContext.Provider value={{ path: "kg/entities/person/pat-mason.md", slug: "126" }}>
      <MdxDoc>{PAGE}</MdxDoc>
    </DocMetaContext.Provider>);

  it("properties: the company is a chip, the email copyable, the date relative", async () => {
    renderPage();
    const props = await screen.findByRole("region", { name: "Entity properties" });
    expect(props.textContent).toContain("Head of Platform");
    expect(screen.getByRole("button", { name: "Copy pat@example.test" })).toBeTruthy();
    expect(props.querySelector("time[datetime='2026-10-10']")).not.toBeNull();
    expect(props.querySelector(".vx-entity")?.textContent).toContain("Acme Bank");
  });

  it("a ticked task is written back to the file; Ask agent sends the task and the page", async () => {
    api.file = PAGE;
    renderPage();
    await screen.findByRole("checkbox", { name: /Done: Send the pricing deck/ });
    // found afresh until the write lands: the page re-renders as its chips resolve
    await waitFor(() => {
      if (!api.writes.length) fireEvent.click(screen.getByRole("checkbox", { name: /Done: Send the pricing deck/ }));
      expect(api.writes.length).toBe(1);
    });
    expect(api.writes[0]).toContain("- [x] Send the pricing deck");
    // every ask is collected (not `once`): an unrelated ask in flight from the page must not be
    // mistaken for this one, which is what made this test flaky under a full run
    const asked: string[] = [];
    const hear = (e: Event) => { const t = (e as CustomEvent).detail?.prompt; if (typeof t === "string") asked.push(t); };
    window.addEventListener(ASK_CHAT_EVENT, hear);
    try {
      // by the task's own text, never by position: the order of the page's buttons is not the claim
      // found afresh on every try: the page re-renders as its links resolve, and a click on a node
      // that render replaced reaches nothing
      await waitFor(() => {
        const task = screen.getByText(/share the SOC 2 report/).closest("li") as HTMLElement;
        fireEvent.click(within(task).getByRole("button", { name: "Ask agent" }));
        expect(asked.some((t) => t.includes("share the SOC 2 report"))).toBe(true);
      });
      expect(asked.find((t) => t.includes("share the SOC 2 report"))).toContain("kg/entities/person/pat-mason.md");
    } finally { window.removeEventListener(ASK_CHAT_EVENT, hear); }
  });

  it("the callout has its title; headings carry anchors", async () => {
    const { container } = renderPage();
    await screen.findByText("Renewal risk");
    expect(container.querySelector("#h-follow-ups")).not.toBeNull();
  });

  it("in a chat reply (no page) tasks are shown, not writable", async () => {
    render(<MdxDoc>{"- [ ] call back"}</MdxDoc>);
    expect(((await screen.findByRole("checkbox")) as HTMLInputElement).disabled).toBe(true);
  });

  it("a ```vexa fence is drawn by the registered block renderer", async () => {
    registerTab("doc.block", ({ params }: { params?: Record<string, unknown> }) => <div data-testid="blocks">{String(params?.source)}</div>);
    render(<MdxDoc>{'```vexa\n{"v":1,"blocks":[]}\n```'}</MdxDoc>);
    expect((await screen.findByTestId("blocks")).textContent).toContain('"v":1');
  });
});

describe("plain names that are pages", () => {
  it("link at the first mention only, never in a heading, a tag or a name already linked", async () => {
    const { entityNamesInProse } = await import("../MdxDoc");
    const names = ["Pat Mason", "Acme Bank", "Board"];
    const out = entityNamesInProse("# Pat Mason\nMet Pat Mason and Pat Mason again. [[Acme Bank]] too. The Board met.", names, new Set(["acme bank"]));
    expect(out).toBe('# Pat Mason\nMet <Wikilink title="Pat Mason" /> and Pat Mason again. [[Acme Bank]] too. The Board met.');
  });
});
