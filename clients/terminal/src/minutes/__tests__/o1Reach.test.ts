/** REACH A COLLEAGUE NOW, IN ONE STEP (founder validation O1, felt on the lab mock 2026-10-18).
 *
 *  Pinned here, pure: no duplicate calls (a call about to start where one of yours is live asks
 *  "<call> is still running. Rejoin it?"), calling a person you are already in a call with asks the
 *  same, calls named after who or where (and, for a call with one person, as each side reads it),
 *  Live → Recent ("ended when everyone left · 12 min"), the live row's "in now" and elapsed time, and
 *  the meeting row's call fields. */
import { describe, it, expect, vi } from "vitest";
import type { MeetingMock } from "../../surfaces/meetingModel";
import { vexaCallFields, type MeetingRowDTO } from "../../surfaces/liveMeetings";
import { liveCallIn, rejoinQuestion, startOrAsk, rejoinAskNow, closeRejoin } from "../callRejoin";
import { viewerCallTitle, meetingDisplayName } from "../callsList";
import { elapsed, endedLine, endedWhy, inNowLine, personCallName, recentCalls, roomCallName, rowLabel } from "../shell2/callNames";
import { callPerson, liveCallPersonDeps } from "../shell2/StartMeeting";

const m = (over: Partial<MeetingMock>): MeetingMock => ({ id: "1", title: "Call", when: "", status: "live", live_status: "active", platform: "vexa_call",
  participants: [], mentioned: [], actions: [], transcript: [], insights: [], ...over });

describe("no duplicate calls", () => {
  it("a live call of yours in My desk is found for a desk start; a room's call is not", () => {
    const desk = m({ id: "7", call_session: "s-desk", title_custom: "Thinking out loud" });
    const room = m({ id: "8", call_session: "s-room", call_room: "s-room" });
    expect(liveCallIn({ kind: "desk" }, [room, desk], null)).toEqual({ session: "s-desk", label: "Thinking out loud", meetingId: "7" });
    expect(liveCallIn({ kind: "desk" }, [room], null)).toBeNull();
    expect(liveCallIn({ kind: "session", session: "s-room" }, [room, desk], null)?.session).toBe("s-room");
  });

  it("someone else's call, an ended call and a bot meeting never count", () => {
    expect(liveCallIn({ kind: "desk" }, [
      m({ call_session: "s-a", shared: true }),
      m({ call_session: "s-b", live_status: "completed" }),
      m({ platform: "Google Meet", call_session: undefined }),
    ], null)).toBeNull();
  });

  it("the call this tab is in counts where it is", () => {
    expect(liveCallIn({ kind: "desk" }, [], { session: "pchat-1", label: "Meeting" })).toEqual({ session: "pchat-1", label: "Meeting" });
    expect(liveCallIn({ kind: "desk" }, [], { session: "s-room", label: "#pilot call", room: true })).toBeNull();
    expect(liveCallIn({ kind: "session", session: "s-room" }, [], { session: "s-room", label: "#pilot call", room: true })?.label).toBe("#pilot call");
  });

  it("nothing live: it starts at once; something live: it asks, and each answer does what it says", () => {
    const start = vi.fn(), rejoin = vi.fn();
    expect(startOrAsk({ kind: "desk" }, { meetings: [], active: null, start, rejoin })).toBe("started");
    expect(start).toHaveBeenCalledTimes(1);
    const live = [m({ id: "9", call_session: "s-x", title_custom: "Call with Megan" })];
    expect(startOrAsk({ kind: "desk" }, { meetings: live, active: null, start, rejoin })).toBe("asked");
    expect(start).toHaveBeenCalledTimes(1);           // no second call yet
    const ask = rejoinAskNow()!;
    expect(rejoinQuestion(ask.label)).toBe("Call with Megan is still running. Rejoin it?");
    ask.onRejoin();
    expect(rejoin).toHaveBeenCalledWith({ session: "s-x", label: "Call with Megan", meetingId: "9" });
    expect(rejoinAskNow()).toBeNull();
    startOrAsk({ kind: "desk" }, { meetings: live, active: null, start, rejoin });
    rejoinAskNow()!.onNew();
    expect(start).toHaveBeenCalledTimes(2);           // a new one only on the explicit answer
    closeRejoin();
  });
});

describe("calling a person you are already in a call with", () => {
  const deps = (live: boolean) => {
    const d = { ...liveCallPersonDeps,
      openRoom: vi.fn(async () => ({ session_id: "s-pair" })),
      getCall: vi.fn(async () => ({ call: live ? { room: "call-1" } : null })),
      post: vi.fn(async (b: Record<string, unknown>) => ({ session_id: "s-pair", already_live: false, with: { name: "Megan Ross" }, call: { room: b.fresh ? "call-2" : "call-1" } })),
      ask: vi.fn(), placed: vi.fn(), open: vi.fn(), join: vi.fn(async () => undefined), fail: vi.fn() };
    return d;
  };

  it("nobody in a call with them: it rings them (the room of two) and the shell joins", async () => {
    const d = deps(false);
    expect(await callPerson({ subject: "u_megan", name: "Megan Ross" }, d)).toBe("rang");
    expect(d.post).toHaveBeenCalledWith({ person: "u_megan" });
    expect(d.placed).toHaveBeenCalledTimes(1);
    expect(d.ask).not.toHaveBeenCalled();
  });

  it("a call already live with them: it asks, Rejoin joins that call, nothing new is started", async () => {
    const d = deps(true);
    expect(await callPerson({ subject: "u_megan", name: "Megan Ross" }, d)).toBe("asked");
    expect(d.post).not.toHaveBeenCalled();
    const a = d.ask.mock.calls[0][0];
    expect(a.label).toBe("Call with Megan");
    a.onRejoin();
    expect(d.open).toHaveBeenCalledWith("s-pair");
    expect(d.join).toHaveBeenCalledWith("s-pair", "Call with Megan");
    expect(d.post).not.toHaveBeenCalled();
  });

  it("Start a new one asks the server for a fresh call (it ends the old one only if nobody else is in it)", async () => {
    const d = deps(true);
    await callPerson({ subject: "u_megan", name: "Megan Ross" }, d);
    d.ask.mock.calls[0][0].onNew();
    await vi.waitFor(() => expect(d.placed).toHaveBeenCalledTimes(1));
    expect(d.post).toHaveBeenCalledWith({ room: "s-pair", fresh: true });
  });
});

describe("a call is named after who or where", () => {
  it("names", () => {
    expect(personCallName("Megan Ross")).toBe("Call with Megan");
    expect(roomCallName("pilot")).toBe("#pilot call");
    expect(roomCallName("#pilot")).toBe("#pilot call");
  });

  it("a call with one person reads 'Call with <the other>' on each side, topic kept", () => {
    const both = [{ subject: "u_dmitry", name: "Dmitry Grankin" }, { subject: "u_megan", name: "Megan Ross" }];
    const meIs = (who: string) => (p: { subject?: string }) => p.subject === who;
    expect(viewerCallTitle("Call with Megan", both, meIs("u_dmitry"))).toBe("Call with Megan");
    expect(viewerCallTitle("Call with Megan", both, meIs("u_megan"))).toBe("Call with Dmitry");
    expect(viewerCallTitle("Call with Megan · ADFS test window", both, meIs("u_megan"))).toBe("Call with Dmitry · ADFS test window");
    expect(viewerCallTitle("#pilot · ADFS test window", both, meIs("u_megan"))).toBe("#pilot · ADFS test window");
    expect(viewerCallTitle("Budget review", both, meIs("u_megan"))).toBe("Budget review");
  });

  it("past calls nobody named are told apart by day and time, not a column of identical names", () => {
    const a = m({ id: "a", live_status: "completed", start_time: "2026-10-15T15:30:00" });
    expect(rowLabel(a, meetingDisplayName(a), "en-GB")).toBe("Call with Vexa · Thu 15:30");
    expect(rowLabel(m({ title_custom: "#pilot call" }), "#pilot call")).toBe("#pilot call");
    expect(rowLabel(m({ platform: "zoom" }), "Call with Vexa")).toBe("Call with Vexa");
  });

  it("the room's name wins over the generic one", () => {
    expect(meetingDisplayName(m({ title: "#pilot call", title_custom: "#pilot call" }))).toBe("#pilot call");
  });

  it("the meeting row carries the call's session, room, end reason, rename and both people", () => {
    const d: MeetingRowDTO = { id: 5, platform: "vexa_call", native_meeting_id: "call-1", status: "completed",
      data: { session_id: "s-pair", completion_reason: "left_alone", title: "#pilot · ADFS test window",
        metadata: { room: "s-pair", call_named: "#pilot call", call_renamed_from: "#pilot call", call_kind: "person",
          call_with: [{ subject: "a", name: "A" }, { subject: "b", name: "B" }] } } };
    expect(vexaCallFields(d)).toEqual({ call_session: "s-pair", call_room: "s-pair", completion_reason: "left_alone",
      renamed_from: "#pilot call", call_with: [{ subject: "a", name: "A" }, { subject: "b", name: "B" }] });
    expect(vexaCallFields({ ...d, platform: "google_meet" })).toEqual({});
  });
});

describe("Live → Recent", () => {
  const now = Date.parse("2026-10-18T12:00:00Z");
  it("an ended call says why and how long", () => {
    expect(endedLine({ completion_reason: "left_alone", start_time: "2026-10-18T11:00:00Z", end_time: "2026-10-18T11:12:00Z" }))
      .toBe("ended when everyone left · 12 min");
    expect(endedWhy("startup_alone")).toBe("nobody joined");
  });

  it("Recent holds the Vexa calls that ended in the last hours, newest first; live ones stay Live", () => {
    const rows = [
      m({ id: "a", live_status: "completed", end_time: "2026-10-18T11:50:00Z" }),
      m({ id: "b", live_status: "completed", end_time: "2026-10-18T09:00:00Z" }),
      m({ id: "c", live_status: "completed", end_time: "2026-10-17T09:00:00Z" }),
      m({ id: "d", live_status: "active" }),
      m({ id: "e", platform: "Google Meet", live_status: "completed", end_time: "2026-10-18T11:55:00Z" }),
    ];
    expect(recentCalls(rows, now).map((r) => r.id)).toEqual(["a", "b"]);
  });

  it("a live row says who is in now and for how long", () => {
    expect(inNowLine(["Megan Ross", "Dmitry Grankin"], (n) => n === "Dmitry Grankin")).toBe("in now: Megan, you");
    expect(inNowLine([], () => false)).toBe("nobody in now");
    expect(elapsed(0, 252_000)).toBe("4:12");
    expect(elapsed(0, 3_900_000)).toBe("1 h 05 min");
  });
});
