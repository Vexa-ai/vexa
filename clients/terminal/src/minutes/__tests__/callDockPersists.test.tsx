/** THE CALL DOCK SURVIVES NAVIGATION (founder, live app.dev 2026-10-18: "call is live but I do not have
 *  call controls here after I surfed the UI"). He was in a call from "Thinking out loud", opened
 *  Meetings, and the docked bar was painted over. Now the same dock floats above any view that covers
 *  the conversation: its controls stay, the room is never unmounted (no drop, no rejoin), there is
 *  only one, and hanging up from it ends the call here. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, act, waitFor } from "@testing-library/react";

const mounts = vi.hoisted(() => ({ n: 0, unmounts: 0 }));
vi.mock("../CallRoom", async () => {
  const React = await import("react");
  return {
    default: (p: { name: string; onLeave: () => void; onName: () => void }) => {
      React.useEffect(() => { mounts.n += 1; return () => { mounts.unmounts += 1; }; }, []);
      return <div data-room="">
        <button type="button" onClick={p.onName}>{p.name}</button>
        <span>4:12</span><span>in now: Megan</span>
        <button type="button" aria-label="Mute">mic</button><button type="button" aria-label="Camera">cam</button>
        <button type="button" aria-label="Share screen">share</button>
        <button type="button" aria-label="Leave the call" onClick={p.onLeave}>leave</button>
      </div>;
    },
  };
});
import { CallDock } from "../CallDock";
import { enterCall, leaveCall, type CallDeps } from "../callState";

const deps: CallDeps = {
  getCall: async () => ({ session_id: "s-1", calls: true, call: null }),
  startCall: async () => ({ session_id: "s-1", calls: true, call: null }),
  joinCall: async () => ({ session_id: "s-1", url: "wss://x", token: "t", role: "owner",
    call: { room: "call-1", meeting_id: 9, owner: "me", url: "wss://x", started: 0, transcribed: true, participants: 2 } }),
};

afterEach(() => { cleanup(); act(() => leaveCall()); mounts.n = 0; mounts.unmounts = 0; });

describe("the call dock across views", () => {
  it("floats with working controls when another view covers the chat, without remounting the room", async () => {
    const onOpenShared = vi.fn();
    await act(async () => { await enterCall("pchat-think", "Thinking out loud", deps); });
    const { rerender } = render(<CallDock onOpenShared={onOpenShared} onOpenPrivate={() => undefined} />);
    await waitFor(() => expect(screen.getByLabelText("Leave the call")).toBeTruthy());
    expect(document.querySelector('[data-call="dock"]')!.hasAttribute("data-floating")).toBe(false);
    // the person opens Meetings: the centre covers the conversation
    rerender(<CallDock onOpenShared={onOpenShared} onOpenPrivate={() => undefined} floating />);
    const dock = document.querySelector('[data-call="dock"]')!;
    expect(dock.hasAttribute("data-floating")).toBe(true);
    expect(dock.className).toContain("vx-calldock-floating");
    for (const l of ["Mute", "Camera", "Share screen", "Leave the call"]) expect(screen.getByLabelText(l)).toBeTruthy();
    expect(document.querySelectorAll('[data-call="dock"]').length).toBe(1);
    expect(mounts.n).toBe(1);                 // the same room: no drop, no rejoin
    expect(mounts.unmounts).toBe(0);
    // its name goes back to the call's chat
    fireEvent.click(screen.getByRole("button", { name: /Thinking out loud|Call/ }));
    expect(onOpenShared).toHaveBeenCalledWith("pchat-think");
  });

  it("hanging up from the floating dock ends the call here", async () => {
    await act(async () => { await enterCall("pchat-think", "Thinking out loud", deps); });
    render(<CallDock onOpenShared={() => undefined} onOpenPrivate={() => undefined} floating />);
    await waitFor(() => expect(screen.getByLabelText("Leave the call")).toBeTruthy());
    fireEvent.click(screen.getByLabelText("Leave the call"));
    await waitFor(() => expect(document.querySelector('[data-call="dock"]')).toBeNull());
    expect(mounts.unmounts).toBe(1);          // the room disconnects
  });
});
