/** Show a document to the room (JTBD-1, rooms): what the room is showing opens in every member's
 *  right panel once per show; a member who closes it is not reopened until the next show. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { setFeatureOverride } from "../../platform/features";
import { resetRoomShow, roomShowing, roomShowState } from "../roomShow";

const shown = (seq: number, by = "u-bob") => ({ by, workspace: "dna", path: "plan.md", title: "Plan", seq });

afterEach(() => { resetRoomShow(); setFeatureOverride("all"); });

describe("roomShowing", () => {
  it("opens the shown page once per show, named by who shows it", () => {
    setFeatureOverride({ rooms: true, home2: true });
    const open = vi.fn();
    expect(roomShowing("s1", shown(1), "u-ann", { "u-bob": "Bob" }, open)).toBe("opened");
    expect(open).toHaveBeenCalledWith("dna", "plan.md", "Plan · shown by Bob");
    expect(roomShowing("s1", shown(1), "u-ann", {}, open)).toBe("same");      // closed by Ann: stays closed
    expect(roomShowing("s1", shown(2), "u-ann", {}, open)).toBe("opened");    // the next show opens again
    expect(open).toHaveBeenCalledTimes(2);
    expect(roomShowState("s1")?.showing?.seq).toBe(2);
  });
  it("does nothing with rooms off, and nothing when nothing is shown", () => {
    const open = vi.fn();
    setFeatureOverride({ rooms: false, home2: true });
    expect(roomShowing("s1", shown(1), "u-ann", {}, open)).toBe("off");
    setFeatureOverride({ rooms: true, home2: true });
    expect(roomShowing("s1", null, "u-ann", {}, open)).toBe("none");
    expect(open).not.toHaveBeenCalled();
  });
});
