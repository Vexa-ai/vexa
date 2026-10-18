/** THE MINUTES TAB — the first half of Vexa-ai/vexa#1588.
 *
 *  The founder opened the meeting chat for DNA TSC 2026-03-02. `drop_to_attendees` had written the
 *  report to `kg/entities/meeting/2026-03-02-0000-dna-tsc-2026-03-02.md` on his desk an hour
 *  earlier. The pinned "Minutes" tab opened `kg/entities/meeting/96088138284.md` — the native-id
 *  spelling this client composed — and said *"No page here yet"*. Two spellings of one path, in two
 *  languages, and only one of them is written by anything.
 *
 *  The pin that matters is against the FLOW'S RECIPE — `kg/entities/meeting/<meeting-day>-<title-slug>.md`,
 *  where the day is rendered in the ORGANISER's timezone and the slug through a server-side
 *  allow-list. Neither is derivable here, which is the whole reason the path is asked for and never
 *  composed. Its sibling defect — the act whose composed prompt leaked into the chat as the
 *  person's own message — is pinned in `actLabel.test.ts`.
 */
import { describe, it, expect, vi } from "vitest";
import { fetchMeetingNote, fetchMeetingNotePath } from "../meetingNote";
import { artifactFromToken, isRetiredNotePath, pagesForPhase } from "../roomView";

/** What `_note_path` produced for the meeting in the ledger — the whole stamp, the slugged title.
 *  Nothing in this file may compose it; it only ever arrives. */
const FLOW_NOTE = "kg/entities/meeting/2026-03-02-0000-dna-tsc-2026-03-02.md";
const NATIVE = "96088138284";
const ROW = "147";

const labels = (pages: { label: string }[]) => pages.map((p) => p.label);
const paths = (pages: { path: string }[]) => pages.map((p) => p.path);

describe("the room's own pages — the notes live in the view, never a guessed tab", () => {
  it("a real meeting opens the meeting view, whatever the server says about its notes", () => {
    for (const note of [FLOW_NOTE, null, undefined]) {
      expect(labels(pagesForPhase("post", NATIVE, ROW, note))).toEqual(["Meeting", "Personal page"]);
    }
  });

  it("never composes the native-id spelling — the page nothing writes", () => {
    for (const phase of ["prep", "live", "post"] as const) {
      expect(paths(pagesForPhase(phase, NATIVE, ROW, FLOW_NOTE)))
        .not.toContain(`kg/entities/meeting/${NATIVE}.md`);
    }
  });

  it("binds the view to the row, with or without a native id", () => {
    expect(pagesForPhase("post", NATIVE, ROW, FLOW_NOTE)[0]).toMatchObject({ kind: "meeting", path: ROW });
    expect(labels(pagesForPhase("post", null, ROW, FLOW_NOTE))).toEqual(["Meeting", "Personal page"]);
  });

  it("agrees with the scaffold's own answer for the same meeting: `meeting:note` IS the view", () => {
    const fromToken = artifactFromToken("meeting:note", { native: NATIVE, notePath: FLOW_NOTE, meetingId: ROW, phase: "post" });
    expect(fromToken).toMatchObject({ kind: "meeting", path: ROW, label: "Meeting" });
    // with no row (a mock), it is the notes file the server named — called Notes, never Minutes
    expect(artifactFromToken("meeting:note", { native: NATIVE, notePath: FLOW_NOTE, meetingId: null, phase: "post" }))
      .toEqual({ path: FLOW_NOTE, label: "Notes" });
  });
});

describe("the retired spelling — recognised so a stored strip can be healed", () => {
  it("names the exact path this client used to mint, for this meeting's native", () => {
    expect(isRetiredNotePath(`kg/entities/meeting/${NATIVE}.md`, NATIVE)).toBe(true);
    expect(isRetiredNotePath(FLOW_NOTE, NATIVE)).toBe(false);
    expect(isRetiredNotePath("kg/entities/meeting/99999.md", NATIVE)).toBe(false);
  });

  it("is never true without a native to compare against", () => {
    expect(isRetiredNotePath("kg/entities/meeting/.md", null)).toBe(false);
    expect(isRetiredNotePath(`kg/entities/meeting/${NATIVE}.md`, "")).toBe(false);
  });
});

describe("asking the server where the note is", () => {
  const res = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response;

  it("asks by row id and returns the path it is given", async () => {
    const asked: string[] = [];
    const f = (async (u: RequestInfo | URL) => { asked.push(String(u)); return res({ path: FLOW_NOTE }); }) as unknown as typeof fetch;
    expect(await fetchMeetingNotePath(ROW, f)).toBe(FLOW_NOTE);
    expect(asked).toEqual([`/api/meeting/note?meeting_id=${ROW}`]);
  });

  it("null is a RESOLVED answer, never an error the room shows", async () => {
    expect(await fetchMeetingNotePath(ROW, (async () => res({ path: null })) as unknown as typeof fetch)).toBeNull();
    expect(await fetchMeetingNotePath(ROW, (async () => res({}, false)) as unknown as typeof fetch)).toBeNull();
    expect(await fetchMeetingNotePath("", vi.fn() as unknown as typeof fetch)).toBeNull();
  });

  it("costs the view its notes and nothing else when the lookup fails", async () => {
    const thrower = (async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await expect(fetchMeetingNotePath(ROW, thrower)).resolves.toBeNull();
  });

  it("refuses a path that walks out of the workspace", async () => {
    const f = (async () => res({ path: "../../etc/passwd" })) as unknown as typeof fetch;
    expect(await fetchMeetingNotePath(ROW, f)).toBeNull();
  });
});

describe("what the server says about the page, beyond where it is", () => {
  const res = (body: unknown, ok = true) => ({ ok, json: async () => body }) as Response;

  it("carries the widget's meeting and the page's cursor through", async () => {
    const f = (async () => res({ path: FLOW_NOTE, transcript: ROW, cursor: "2026-09-06T12:04:31Z" })) as unknown as typeof fetch;
    expect(await fetchMeetingNote(ROW, f)).toEqual({ workspace: "", title: "", path: FLOW_NOTE, transcript: ROW, cursor: "2026-09-06T12:04:31Z" });
  });

  it("a page written before the widget answers with neither, and that is not a failure", async () => {
    const f = (async () => res({ path: FLOW_NOTE })) as unknown as typeof fetch;
    expect(await fetchMeetingNote(ROW, f)).toEqual({ workspace: "", title: "", path: FLOW_NOTE, transcript: "", cursor: "" });
  });

  it("a widget without a path is dropped — it could only hide a tab with nothing behind it", async () => {
    const f = (async () => res({ path: null, transcript: ROW })) as unknown as typeof fetch;
    expect(await fetchMeetingNote(ROW, f)).toEqual({ workspace: "", title: "", path: null, transcript: "", cursor: "" });
    const walker = (async () => res({ path: "../../etc/passwd", transcript: ROW })) as unknown as typeof fetch;
    expect(await fetchMeetingNote(ROW, walker)).toEqual({ workspace: "", title: "", path: null, transcript: "", cursor: "" });
  });

  it("an unreachable server costs the room its Minutes tab and nothing else", async () => {
    const thrower = (async () => { throw new Error("offline"); }) as unknown as typeof fetch;
    await expect(fetchMeetingNote(ROW, thrower)).resolves.toEqual({ workspace: "", title: "", path: null, transcript: "", cursor: "" });
  });
});
