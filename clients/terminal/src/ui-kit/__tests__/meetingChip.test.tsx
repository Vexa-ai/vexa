/** THE MEETING CHIP — a meeting named in a reply renders as the meeting, never as a filename.
 *
 *  Founder, 2026-10-11, on a chat reply where the people were chips and the meeting was the file
 *  `2026-10-09-1401-zoom-meeting-88410021706.md`: *"i think we need a special label for a meeting
 *  alongside people companies etc"*.
 *
 *  Pinned end to end from the words in a reply: a link to the notes page, a `?meeting=` link, a
 *  backticked path, a `[[wikilink]]` that lands on the notes — each becomes the chip with the
 *  meeting's own title and date; a meeting this reader may not see becomes plain text with no title.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Markdown } from "../Markdown";
import { DocPath, Wikilink, invalidateDocLinkCaches } from "../docLinks";
import { invalidateWsLinkCaches } from "../wsLinks";
import { meetingDay, meetingNotePath, meetingRefOfHref, platformName } from "../MeetingChip";
import { OPEN_MEETING_EVENT } from "../../platform";

const NOTE = "kg/entities/meeting/2026-10-09-1401-zoom-meeting-88410021706.md";
const STRANGERS = "kg/entities/meeting/2026-10-08-0900-board.md";
const ROW = { id: "118", platform: "Zoom", native_id: "88410021706", title: "Campbell", title_custom: "Campbell",
  start_time: "2026-10-09T14:01:00Z", status: "past" };

vi.mock("../../surfaces/liveMeetings", () => ({
  useLiveMeetings: () => [ROW],
  useLiveMeetingsLoaded: () => true,
  ensureMeetingKnown: vi.fn(),
}));
const asked: string[][] = [];
vi.mock("../../surfaces/workspaceApi", () => ({
  listWorkspaceTree: vi.fn(async () => [NOTE]),
  readActiveSet: vi.fn(async () => ({ subject: "126", active: [{ slug: "seed", path: "/workspaces/126", primary: true }] })),
  listSharedMemberships: vi.fn(async () => []),
  // The server's answer (core/agent/control_plane/meeting_link.py): the meeting for the owner's
  // notes, `not-yours` with no title for a meeting this reader may not see.
  resolveLinks: vi.fn(async (refs: string[]) => {
    asked.push(refs);
    return refs.map((ref) => (ref.endsWith(NOTE)
      ? { ref, kind: "meeting", access: "readable", title: "Campbell", url: null, at: "2026-10-09T14:01:00Z",
          platform: "zoom", path: NOTE, meeting: { id: "118", platform: "zoom", native: "88410021706" } }
      : { ref, kind: "meeting", access: "not-yours", title: "", url: null }));
  }),
}));

let opened: string[] = [];
const capture = (e: Event) => { opened.push(String((e as CustomEvent<{ ref?: string }>).detail?.ref)); };
beforeEach(() => {
  opened = [];
  asked.length = 0;
  invalidateDocLinkCaches();
  invalidateWsLinkCaches();
  window.addEventListener(OPEN_MEETING_EVENT, capture);
});
afterEach(() => { window.removeEventListener(OPEN_MEETING_EVENT, capture); cleanup(); });

const chip = () => document.querySelector("[data-meeting-chip]") as HTMLElement | null;

describe("a meeting named in a reply", () => {
  it("a link to its notes page is the meeting chip: its title and date, never the filename", async () => {
    const { container } = render(<Markdown>{`We met in [${NOTE.split("/").pop()}](${NOTE}) with [[James Markakis]].`}</Markdown>);
    await waitFor(() => expect(chip()).not.toBeNull());
    expect(chip()!.textContent).toContain("Campbell");
    expect(chip()!.querySelector("[data-meeting-day]")!.textContent).toContain("Fri 9 Oct");
    expect(container.textContent).not.toContain(".md");
    expect(asked.flat()).toContain(NOTE);
    fireEvent.click(chip()!);
    expect(opened).toEqual(["118"]);           // the meeting, by row id — the route the Calls list uses
  });

  it("a backticked notes path is the chip too", async () => {
    const { container } = render(<Markdown>{`See \`${NOTE}\`.`}</Markdown>);
    await waitFor(() => expect(chip()?.textContent).toContain("Campbell"));
    expect(container.textContent).not.toContain("zoom-meeting-88410021706");
  });

  it("a ?meeting= link is the chip with the meeting's current title, whatever the link says", () => {
    render(<Markdown>{"Recorded in [the call](/?meeting=118)."}</Markdown>);
    expect(chip()!.textContent).toContain("Campbell");
    expect(chip()!.textContent).toContain("Fri 9 Oct");
    expect(chip()!.textContent).not.toContain("the call");
    fireEvent.click(chip()!);
    expect(opened).toEqual(["118"]);
  });

  it("a <platform>/<native> link finds the same meeting", () => {
    render(<Markdown>{"[x](https://app.example/?meeting=zoom/88410021706)"}</Markdown>);
    expect(chip()!.textContent).toContain("Campbell");
  });

  it("DocPath (the MDX renderer's path chip) names the meeting, not the file", async () => {
    render(<DocPath path={`/workspaces/126/${NOTE}`} />);
    await waitFor(() => expect(chip()?.textContent).toContain("Campbell"));
  });

  it("a [[wikilink]] that lands on the notes is the meeting chip", async () => {
    render(<Wikilink title="2026-10-09-1401-zoom-meeting-88410021706" />);
    await waitFor(() => expect(chip()?.textContent).toContain("Campbell"));
  });
});

describe("a meeting this reader may not see", () => {
  it("a notes link the server answers not-yours is plain text — no title, no filename", async () => {
    const { container } = render(<Markdown>{`Before that, [${STRANGERS.split("/").pop()}](${STRANGERS}).`}</Markdown>);
    await waitFor(() => expect(container.querySelector("[data-meeting-plain]")).not.toBeNull());
    expect(chip()).toBeNull();
    expect(container.textContent).toContain("a meeting");
    expect(container.textContent).not.toContain("board");
    expect(container.textContent).not.toContain(".md");
  });

  it("a ?meeting= link not in the reader's meetings list is the writer's words, plain", () => {
    const { container } = render(<Markdown>{"Also [the board call](/?meeting=999)."}</Markdown>);
    expect(chip()).toBeNull();
    expect(container.querySelector("[data-meeting-plain]")!.textContent).toBe("the board call");
  });
});

describe("the vocabulary", () => {
  it("knows a notes page in every spelling, and nothing else", () => {
    expect(meetingNotePath(NOTE)).toBe(NOTE);
    expect(meetingNotePath(`./${NOTE}`)).toBe(NOTE);
    expect(meetingNotePath(`/workspaces/126/${NOTE}#top`)).toBe(NOTE);
    expect(meetingNotePath("kg/entities/meeting/index.md")).toBeNull();
    expect(meetingNotePath("kg/entities/person/james-markakis.md")).toBeNull();
    expect(meetingNotePath("kg/entities/meeting/a/b.md")).toBeNull();
  });
  it("reads a ?meeting= href, relative or absolute", () => {
    expect(meetingRefOfHref("/?meeting=118")).toBe("118");
    expect(meetingRefOfHref("https://app.dev.vexa.ai/?ask=prep&meeting=zoom%2F884")).toBe("zoom/884");
    expect(meetingRefOfHref("kg/x.md")).toBeNull();
  });
  it("dates read as a day, with the year only when it is not this one", () => {
    const now = new Date("2026-10-11T12:00:00Z");
    expect(meetingDay("2026-10-09T14:01:00Z", now)).toBe("Fri 9 Oct");
    expect(meetingDay("2025-10-09", now)).toBe("Thu 9 Oct 2025");
    expect(meetingDay("", now)).toBe("");
    expect(meetingDay("not a date", now)).toBe("");
    expect(platformName("google_meet")).toBe("Google Meet");
    expect(platformName("unknown")).toBe("Meeting");
  });
});
