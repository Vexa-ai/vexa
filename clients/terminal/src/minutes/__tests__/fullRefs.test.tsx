/** FULL REFERENCES, RENDERED AS BADGES (founder 2026-10-18; UI 7gg, 7mm).
 *
 *  "Chat about this" on a meeting put `About the meeting "Call with Vexa":` in the composer — and he
 *  has many meetings by that name. Every reference is now the object's full reference, drawn as a
 *  badge (icon · name · disambiguator) for whoever reads it: never a bare title, never a raw id. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const answers: Record<string, unknown> = {};
vi.mock("../../data/workspaceApi", () => ({
  resolveRef: vi.fn(async (ref: string) => answers[ref] ?? { access: "gone", kind: "chat", id: "x", label: null, open: null, request_access: false, url: "/r/chat/x" }),
  requestRefAccess: vi.fn(async () => ({ requested: true })),
  resolveLinks: vi.fn(async () => []),
  listWorkspaceTree: vi.fn(async () => []),
  readActiveSet: vi.fn(async () => ({ subject: "u", active: [] })),
  readWorkspaceGitDiff: vi.fn(async () => ({ sha: "abc1234def", diff: "+added line" })),
}));

import { ChatAboutButton } from "../shell2/ChatAbout";
import { CHAT_ABOUT_EVENT, chatAboutPrompt, type Artifact } from "../shell2/about";
import { meetingRowId } from "../../canvas/MeetingView";
import { RefChip, OPEN_COMMIT_EVENT, textWithRefs } from "../../ui-kit/RefChip";
import { invalidateRefCache, liftRefs, refHint } from "../../ui-kit/universalRefs";
import { clipboardPayload, referenceFromPaste } from "../../surfaces/reference";
import { withRefs } from "../../surfaces/chat";
import { personRef } from "../shell2/PersonPanel";

const meeting = (id: number, at: string) => ({
  access: "readable", kind: "meeting", id: String(id), label: "Call with Vexa", open: { meeting: id },
  request_access: false, url: `/r/meeting/${id}`, hint: { at, by: "Dmitry" },
});

beforeEach(async () => {
  for (const k of Object.keys(answers)) delete answers[k]; invalidateRefCache(); try { localStorage.clear(); } catch { /* none */ }
  // load the (mocked) api once up front: concurrent first dynamic imports race the mock in vitest
  await import("../../data/workspaceApi");
});
afterEach(cleanup);

describe("Chat about this names the meeting, not its title", () => {
  it("two meetings called the same: the second one's button inserts the second one's reference", () => {
    const seen: Artifact[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent<Artifact>).detail);
    window.addEventListener(CHAT_ABOUT_EVENT, on);
    const a = (id: string) => ({ key: `meeting:${id}`, path: `meeting:${id}`, label: "Call with Vexa", kind: "meeting" as const,
      ref: `[[meeting:${meetingRowId({ id }, id)}]]` });
    render(<><div data-testid="m1"><ChatAboutButton a={a("201")} /></div><div data-testid="m2"><ChatAboutButton a={a("202")} /></div></>);
    fireEvent.click(screen.getByTestId("m2").querySelector("button")!);
    window.removeEventListener(CHAT_ABOUT_EVENT, on);
    const prompt = chatAboutPrompt(seen.at(-1)!);
    expect(prompt).toBe("[[meeting:202]] ");
    expect(prompt).not.toContain("Call with Vexa");
    // the composer lifts it into a badge and sends it ahead of the words
    const lifted = liftRefs(prompt);
    expect(lifted).toEqual({ refs: ["[[meeting:202]]"], text: "" });
    expect(withRefs("what was decided?", lifted.refs)).toBe("[[meeting:202]]\n\nwhat was decided?");
  });

  it("a meeting opened by its native id still references the row", () => {
    expect(meetingRowId({ id: "202" }, "abc-defg-hij")).toBe("202");
    expect(meetingRowId(undefined, "202")).toBe("202");
  });

  it("a page is qualified by its workspace, the desk as personal", () => {
    expect(chatAboutPrompt({ kind: "page", slug: "narrative", path: "README.md" })).toBe("[[page:narrative/README.md]] ");
    expect(chatAboutPrompt({ kind: "page", slug: undefined, path: "kg/plan.md" })).toBe("[[page:personal/kg/plan.md]] ");
    expect(chatAboutPrompt({ kind: "card", path: "", ref: "[[card:k1]]" })).toBe("[[card:k1]] ");
  });
});

describe("the badge: icon, name and a disambiguator — never the id", () => {
  it("two meetings with one title read apart by their time and owner", async () => {
    answers["[[meeting:201]]"] = meeting(201, "2026-10-11T09:00:00Z");
    answers["[[meeting:202]]"] = meeting(202, "2026-10-18T15:26:00Z");
    const { container } = render(<><RefChip text="[[meeting:201]]" /><RefChip text="[[meeting:202]]" /></>);
    await waitFor(() => expect(screen.getAllByText("Call with Vexa")).toHaveLength(2));
    const hints = [...container.querySelectorAll("[data-ref-hint]")].map((h) => h.textContent);
    expect(hints).toHaveLength(2);
    expect(hints[0]).not.toEqual(hints[1]);
    expect(hints[1]).toMatch(/18 Oct \d\d:\d\d · Dmitry/);
    expect(container.textContent).not.toMatch(/\b20[12]\b/);
  });

  it("no raw id text in the DOM for any known kind", async () => {
    const cases: [string, Record<string, unknown>][] = [
      ["[[page:ws-8f2a/kg/plan.md]]", { kind: "page", label: "Plan", hint: { workspace: "Narrative" }, open: { path: "kg/plan.md", slug: "ws-8f2a" } }],
      ["[[person:u-77aa91]]", { kind: "person", label: "Maya Lind", hint: { role: "editor" }, open: { person: "u-77aa91" } }],
      ["[[chat:c-5be0f1]]", { kind: "chat", label: "Board prep", open: { chat: "c-5be0f1" } }],
      ["[[room:s-ed8ddc9d]]", { kind: "room", label: "tester-b", open: { chat: "s-ed8ddc9d" } }],
      ["[[card:k-0d3c22]]", { kind: "card", label: "Renew contract", hint: { state: "waiting" }, open: { card: "k-0d3c22" } }],
      ["[[workspace:ws-8f2a]]", { kind: "workspace", label: "Narrative", open: { workspace: "narrative" } }],
      ["[[routine:flow-91c2]]", { kind: "routine", label: "Weekly digest", open: { flow: "flow-91c2" } }],
      ["[[policy:ws-8f2a/policy/sharing.md]]", { kind: "policy", label: "Sharing", hint: { workspace: "Narrative" }, open: { path: "policy/sharing.md" } }],
      ["[[commit:ws-8f2a/abc1234def567]]", { kind: "commit", label: "Update the plan", hint: { sha: "abc1234" }, open: { commit: "abc1234def567", slug: "ws-8f2a" } }],
      ["[[meeting:4471]]", { kind: "meeting", label: "Untitled meeting", hint: { at: "2026-10-18T15:26:00Z" }, open: { meeting: 4471 } }],
    ];
    for (const [ref, a] of cases) answers[ref] = { access: "readable", id: "x", request_access: false, url: "/r/x", ...a };
    const { container } = render(<>{cases.map(([ref]) => <RefChip key={ref} text={ref} />)}</>);
    await waitFor(() => expect(screen.getByText("Untitled meeting")).toBeTruthy());
    const text = container.textContent ?? "";
    for (const id of ["ws-8f2a", "u-77aa91", "c-5be0f1", "s-ed8ddc9d", "k-0d3c22", "flow-91c2", "abc1234def567", "4471", "[["]) expect(text).not.toContain(id);
    // …while each badge still carries its reference for copy and drag
    expect(container.querySelectorAll("[data-ref]")).toHaveLength(cases.length);
  });

  it("a reference that resolves to nothing is a muted Unavailable badge, never raw syntax", async () => {
    const { container } = render(<RefChip text="[[meeting:999]]" />);
    expect(await screen.findByText("Unavailable")).toBeTruthy();
    expect(container.textContent).not.toContain("[[");
    expect(container.textContent).not.toContain("999");
  });

  it("a commit badge is its message and short hash, and opens the commit review", async () => {
    answers["[[commit:narrative/abc1234def]]"] = { access: "readable", kind: "commit", id: "narrative/abc1234def", label: "Update the plan",
      hint: { sha: "abc1234" }, open: { commit: "abc1234def", slug: "narrative" }, request_access: false, url: "/r/commit/x" };
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_COMMIT_EVENT, on);
    render(<RefChip text="[[commit:narrative/abc1234def]]" />);
    fireEvent.click(await screen.findByText("Update the plan"));
    window.removeEventListener(OPEN_COMMIT_EVENT, on);
    expect(screen.getByText("· abc1234")).toBeTruthy();
    expect(opened).toEqual([{ sha: "abc1234def", slug: "narrative" }]);
    expect(await screen.findByText("+added line")).toBeTruthy();
  });

  it("the disambiguator per kind", () => {
    expect(refHint("page", { workspace: "Narrative" })).toBe("Narrative");
    expect(refHint("person", { role: "editor", workspace: "Narrative" })).toBe("editor");
    expect(refHint("commit", { sha: "abc1234" })).toBe("abc1234");
    expect(refHint("meeting", {})).toBe("");
  });
});

describe("copy reference: from a profile, into the chat, as a badge", () => {
  it("a profile's Copy reference pastes into the composer as the person's badge", async () => {
    const payload = clipboardPayload({ kind: "person", id: "maya@example.com", label: "Maya Lind" });
    expect(payload.chip).toBe("[[person:maya@example.com]]");
    const pasted = referenceFromPaste({ getData: (t: string) => (t === "text/html" ? payload.html : payload.text) });
    const lifted = liftRefs(pasted ?? "");
    expect(lifted.refs).toEqual(["[[person:maya@example.com]]"]);
    answers["[[person:maya@example.com]]"] = { access: "readable", kind: "person", id: "maya@example.com", label: "Maya Lind",
      hint: { role: "editor" }, open: { person: "maya@example.com" }, request_access: false, url: "/r/person/x" };
    const { container } = render(<RefChip text={lifted.refs[0]} />);
    expect(await screen.findByText("Maya Lind")).toBeTruthy();
    expect(container.textContent).not.toContain("maya@example.com");
  });

  it("the plain URL alone (pasted from outside) becomes the same badge", () => {
    const payload = clipboardPayload({ kind: "meeting", id: "202" });
    expect(liftRefs(referenceFromPaste({ getData: (t: string) => (t === "text/plain" ? payload.text : "") }) ?? "").refs).toEqual(["[[meeting:202]]"]);
  });

  it("a non-member sees Restricted: no name, no content, no id", async () => {
    answers["[[person:u-secret|Typed Name]]"] = { access: "not-yours", kind: "person", id: "u-secret", label: null, open: null, request_access: false, url: "/r/person/u-secret" };
    const { container } = render(<RefChip text="[[person:u-secret|Typed Name]]" />);
    expect(await screen.findByText("Restricted")).toBeTruthy();
    expect(container.textContent).not.toMatch(/Typed Name|u-secret/);
  });

  it("copying a selection that holds a badge copies its reference, not its name", async () => {
    answers["[[meeting:202]]"] = meeting(202, "2026-10-18T15:26:00Z");
    const { container } = render(<p>see <RefChip text="[[meeting:202]]" /> today</p>);
    await screen.findByText("Call with Vexa");
    expect(textWithRefs(container.querySelector("p")!)).toBe("see [[meeting:202]] today");
  });

  it("a person is referenced by address, never by bare name", () => {
    expect(personRef({ email: "maya@example.com", subject: "u7", name: "Maya Lind" })).toBe("[[person:maya@example.com]]");
    expect(personRef({ subject: "u7", name: "Maya" })).toBe("[[person:u7]]");
    // a labelled reference pasted in is stored in the one format, without the name
    expect(liftRefs("[[meeting:202|Call with Vexa]] hi").refs).toEqual(["[[meeting:202]]"]);
  });
});
