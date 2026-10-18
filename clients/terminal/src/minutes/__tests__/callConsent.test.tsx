/** A call connects only after the participant has read the transcription notice and pressed Join:
 *  before that there is no room connection and no microphone. Inside the room Vexa shows as a small
 *  listening mark (not a row in a list), and the dock is one compact bar of icon controls. */
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ActiveCall } from "../callState";

const rooms: { connect?: boolean; audio?: unknown; theme?: unknown }[] = [];
vi.mock("@livekit/components-styles", () => ({}));
const local = { identity: "2", setMicrophoneEnabled: vi.fn(async () => {}), setCameraEnabled: vi.fn(async () => {}), setScreenShareEnabled: vi.fn(async () => {}) };
const people = [
  { identity: "2", name: "Dmitry", isLocal: true, isMicrophoneEnabled: true },
  { identity: "60", name: "Valeriia", isLocal: false, isMicrophoneEnabled: false },
  { identity: "vexa-listener", name: "Vexa", isLocal: false, isMicrophoneEnabled: false },
];
vi.mock("@livekit/components-react", () => ({
  LiveKitRoom: (p: { connect?: boolean; audio?: unknown; children?: React.ReactNode; "data-lk-theme"?: string }) => {
    rooms.push({ connect: p.connect, audio: p.audio, theme: p["data-lk-theme"] });
    return <div data-testid="lk-room">{p.children}</div>;
  },
  RoomAudioRenderer: () => null,
  MediaDeviceSelect: (p: { kind: string }) => <ul data-kind={p.kind} />,
  VideoTrack: () => <video />,
  isTrackReference: () => true,
  useLocalParticipant: () => ({ localParticipant: local, isMicrophoneEnabled: true, isCameraEnabled: false, isScreenShareEnabled: false }),
  useParticipants: () => people,
  useSpeakingParticipants: () => [people[1]],
  useRoomInfo: () => ({ metadata: '{"transcribed":true}' }),
  useDataChannel: () => ({ send: vi.fn(async () => {}) }),
  useTracks: () => [],
}));

const call = (transcribed: boolean, role = "member"): ActiveCall => ({
  session: "pchat-1", label: "Team",
  join: { session_id: "s-1", url: "wss://calls.test", token: "tok", role,
          call: { room: "call-abc123", meeting_id: 41, owner: "bob", url: "wss://calls.test", started: 1,
                  transcribed, participants: 1 } },
} as ActiveCall);

afterEach(() => { cleanup(); rooms.length = 0; vi.clearAllMocks(); });
// The room module is loaded once, up front, with room to spare: under a loaded machine the first
// test's own import was what ran out of time, not anything it checks.
beforeAll(async () => { await import("../CallRoom"); }, 60_000);

describe("joining a call", () => {
  it("shows the notice and does not connect or open the microphone before Join", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    render(<CallRoom call={call(true)} onLeave={() => {}} name="AIT team" onPrivate={() => {}} />);
    expect(screen.getByText(/This call is transcribed/)).toBeTruthy();
    expect(screen.queryByTestId("lk-room")).toBeNull();
    expect(rooms.filter((r) => r.connect || r.audio)).toEqual([]);
  });

  it("connects with the microphone once Join is pressed, and lists the transcriber", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    render(<CallRoom call={call(true)} onLeave={() => {}} name="AIT team" onPrivate={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(screen.getByTestId("lk-room")).toBeTruthy();
    expect(rooms.at(-1)?.connect).toBe(true);
    expect(rooms.at(-1)?.audio).toBeTruthy();
    expect(screen.getByLabelText(/Vexa is listening/)).toBeTruthy();
  });

  it("Cancel leaves without connecting", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    const onLeave = vi.fn();
    render(<CallRoom call={call(true)} onLeave={onLeave} name="AIT team" onPrivate={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onLeave).toHaveBeenCalled();
    expect(rooms).toEqual([]);
  });

  it("asks for Join even when the call is not transcribed, and then shows no transcriber", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    render(<CallRoom call={call(false)} onLeave={() => {}} name="AIT team" onPrivate={() => {}} />);
    expect(screen.queryByTestId("lk-room")).toBeNull();
    expect(screen.queryByText(/This call is transcribed/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(rooms.at(-1)?.connect).toBe(true);
  });
});

describe("the dock is one compact bar (UI decisions 7–10a, 14)", () => {
  const join = async () => {
    const { default: CallRoom } = await import("../CallRoom");
    const r = render(<CallRoom call={call(true)} name="AIT team" onLeave={() => {}} onPrivate={() => {}} onAddPeople={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    return r;
  };

  it("names the call, shows who is in it and who speaks, and Vexa is a mark, not a person", async () => {
    const { container } = await join();
    expect(screen.getByText("AIT team")).toBeTruthy();
    expect(container.textContent).not.toContain("Call · ");
    const roster = screen.getByLabelText(/In the call:/);
    expect(roster.getAttribute("aria-label")).toBe("In the call: Dmitry (you), Valeriia");
    expect(container.querySelector('[data-speaking] img, .vx-ck-avatar[data-speaking]')).not.toBeNull();
    expect(container.textContent).not.toMatch(/Vexa transcription/);
  });

  it("has icon controls with tooltips — no big text buttons, no share-to-the-room box, no transcript", async () => {
    const { container } = await join();
    for (const label of ["Mute", "Turn camera on", "Share your screen", "More", "Leave the call"]) {
      const b = screen.getByRole("button", { name: label });
      expect(b.getAttribute("title")).toBe(label);
      expect(b.textContent).toBe("");
    }
    expect(container.querySelector("input, textarea")).toBeNull();
    expect(container.textContent).not.toMatch(/Share to the room|Private|Devices/);
    expect(container.querySelector(".vx-ck-transcript")).toBeNull();
  });

  it("the controls drive the local participant; devices, the meeting and adding people sit in …", async () => {
    await join();
    fireEvent.click(screen.getByRole("button", { name: "Mute" }));
    expect(local.setMicrophoneEnabled).toHaveBeenCalledWith(false);
    fireEvent.click(screen.getByRole("button", { name: "Turn camera on" }));
    expect(local.setCameraEnabled).toHaveBeenCalledWith(true);
    fireEvent.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("menuitem", { name: "Notes and transcript" })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: "Add people" })).toBeTruthy();
    expect(document.querySelector('[data-kind="audioinput"]')).not.toBeNull();
  });

  it("the transcription notice before joining is one quiet line, not a banner", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    const { container } = render(<CallRoom call={call(true)} name="AIT team" onLeave={() => {}} onPrivate={() => {}} />);
    const note = container.querySelector('[data-call="transcribed"]') as HTMLElement;
    expect(note.textContent).toMatch(/This call is transcribed · everyone in it sees this/);
    expect(note.className).toBe("vx-cb-quiet");
  });
});

describe("joining opens the call's meeting beside the chat; the bar is readable in both themes", () => {
  it("Join opens the meeting in the right panel (transcript, notes, shown page) — once", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    const onOpenMeeting = vi.fn();
    render(<CallRoom call={call(true)} name="AIT team" onLeave={() => {}} onPrivate={() => {}} onOpenMeeting={onOpenMeeting} />);
    expect(onOpenMeeting).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(onOpenMeeting).toHaveBeenCalledTimes(1);
  });
  it("the room carries no LiveKit theme (its dark background made the name dark-on-black)", async () => {
    const { default: CallRoom } = await import("../CallRoom");
    render(<CallRoom call={call(true)} name="AIT team" onLeave={() => {}} onPrivate={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(rooms.at(-1)?.theme).toBeUndefined();
  });
});
