/** Browser state belongs to the account that wrote it. One browser, two accounts in turn: the
 *  second never reads what the first stored (an open workspace, a pending meeting, a layout). */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  ACCOUNT_KEY, adoptAccount, installAccountScope, scopedKey, uninstallAccountScope,
} from "../accountStorage";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
afterEach(() => { uninstallAccountScope(); localStorage.clear(); sessionStorage.clear(); });

describe("account-scoped browser storage", () => {
  it("a key written by one account is not read by the next", () => {
    installAccountScope();
    expect(adoptAccount("36")).toBe("first");
    localStorage.setItem("vexa.openWorkspace", "team-abc123");
    sessionStorage.setItem("vexa.readme.pinned.team-abc123", "1");
    expect(localStorage.getItem("vexa.openWorkspace")).toBe("team-abc123");

    expect(adoptAccount("39")).toBe("changed");
    expect(localStorage.getItem("vexa.openWorkspace")).toBeNull();
    expect(sessionStorage.getItem("vexa.readme.pinned.team-abc123")).toBeNull();
  });

  it("an account finds its own state again after another account used the browser", () => {
    installAccountScope();
    adoptAccount("36");
    localStorage.setItem("vexa.terminal.dock.v3", "layout-36");
    adoptAccount("39");
    localStorage.setItem("vexa.terminal.dock.v3", "layout-39");
    adoptAccount("36");
    expect(localStorage.getItem("vexa.terminal.dock.v3")).toBe("layout-36");
  });

  it("unscoped legacy keys are cleared when an account is adopted, never applied", () => {
    localStorage.setItem("vexa.openWorkspace", "team-abc123");
    localStorage.setItem("vexa.pendingPreset", "{}");
    sessionStorage.setItem("vexa.readme.pinned.x", "1");
    localStorage.setItem("unrelated", "kept");
    installAccountScope();
    adoptAccount("39");
    expect(localStorage.getItem("vexa.openWorkspace")).toBeNull();
    uninstallAccountScope();
    expect(localStorage.getItem("vexa.openWorkspace")).toBeNull();
    expect(localStorage.getItem("vexa.pendingPreset")).toBeNull();
    expect(sessionStorage.getItem("vexa.readme.pinned.x")).toBeNull();
    expect(localStorage.getItem("unrelated")).toBe("kept");
  });

  it("the same account on a reload changes nothing", () => {
    installAccountScope();
    adoptAccount("36");
    localStorage.setItem("vexa.openMeetingRef", "google_meet/abc");
    uninstallAccountScope();
    installAccountScope();                       // a reload: the last account is the scope at boot
    expect(localStorage.getItem("vexa.openMeetingRef")).toBe("google_meet/abc");
    expect(adoptAccount("36")).toBe("same");
    expect(localStorage.getItem("vexa.openMeetingRef")).toBe("google_meet/abc");
  });

  it("keys outside the vexa namespace, the theme and the account marker are not scoped", () => {
    expect(scopedKey("other", "36")).toBe("other");
    expect(scopedKey("vexa.terminal.theme", "36")).toBe("vexa.terminal.theme");
    expect(scopedKey(ACCOUNT_KEY, "36")).toBe(ACCOUNT_KEY);
    expect(scopedKey("vexa.openWorkspace", "36")).not.toBe("vexa.openWorkspace");
  });

  it("an unknown account reads nothing another account stored", () => {
    installAccountScope();
    adoptAccount("36");
    localStorage.setItem("vexa.openWorkspace", "team-abc123");
    adoptAccount(null);
    expect(localStorage.getItem("vexa.openWorkspace")).toBeNull();
  });

  it("an unverified account after a known one forgets the account and asks for a reload", () => {
    installAccountScope();
    adoptAccount("36");
    localStorage.setItem("vexa.openWorkspace", "team-abc123");
    uninstallAccountScope();
    installAccountScope();                       // a reload: 36's scope is active at boot
    expect(localStorage.getItem("vexa.openWorkspace")).toBe("team-abc123");
    expect(adoptAccount(null)).toBe("changed");  // the caller reloads before anything renders
    uninstallAccountScope();
    expect(localStorage.getItem(ACCOUNT_KEY)).toBeNull();
    installAccountScope();                       // after the reload nothing of 36's is in scope
    expect(localStorage.getItem("vexa.openWorkspace")).toBeNull();
    expect(adoptAccount(null)).toBe("unverified"); // and the next answer does not reload again
  });
});
