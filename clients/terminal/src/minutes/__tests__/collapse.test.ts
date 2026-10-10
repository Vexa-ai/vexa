/** Both side columns fold away, and the choice is remembered — now under ONE key, `vexa.shell.v1`
 *  (ui-kit/layout/useShellLayout). The old per-side keys are read once, as a migration, so a reader
 *  who folded a column before the move finds it folded after. A profile that has never chosen must
 *  open with all three columns rather than with two sides missing. */
import { beforeEach, describe, expect, it } from "vitest";
import { COLLAPSED_KEY, LEGACY_PAGES_W_KEY, legacyShellStore, loadCollapsed } from "../chats";
import { readShellStore, SHELL_KEY, writeShellStore } from "../../ui-kit/layout/useShellLayout";

describe("side-column collapse — migrated into the shell store", () => {
  beforeEach(() => localStorage.clear());

  it("a profile that has never chosen opens with both columns", () => {
    expect(loadCollapsed("left")).toBe(false);
    expect(loadCollapsed("right")).toBe(false);
    expect(readShellStore(legacyShellStore).prefs).toEqual({ railOpen: true, pagesOpen: true });
  });

  it("a fold made under the old keys survives the move, per side", () => {
    localStorage.setItem(COLLAPSED_KEY.right, "1");
    expect(readShellStore(legacyShellStore).prefs).toEqual({ railOpen: true, pagesOpen: false });
  });

  it("the old pages width comes along for the modes the panel docked at", () => {
    localStorage.setItem(LEGACY_PAGES_W_KEY, "612");
    expect(readShellStore(legacyShellStore).widths).toEqual({ wide: { pages: 612 }, desktop: { pages: 612 } });
  });

  it("anything but an explicit \"1\" reads as OPEN — a junk value never hides a column", () => {
    localStorage.setItem(COLLAPSED_KEY.left, "true");
    expect(loadCollapsed("left")).toBe(false);
  });

  it("once the new store exists, the old keys are not consulted again", () => {
    writeShellStore({ prefs: { railOpen: true, pagesOpen: true }, widths: {} });
    localStorage.setItem(COLLAPSED_KEY.left, "1");
    expect(readShellStore(legacyShellStore).prefs.railOpen).toBe(true);
    expect(localStorage.getItem(SHELL_KEY)).toContain("railOpen");
  });
});
