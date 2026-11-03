/** WHAT AIMING THE HOME COMPOSER MAY RE-TARGET (#1783, R3 `home2`).
 *
 *  A status card or the workspace page's "New chat" aims the home's new chat at a workspace. It must
 *  never re-target a stored conversation that happens to be in front — that would skip the chat's
 *  own membership guard and move where an existing conversation writes. So: the untouched draft in
 *  front is aimed; anything else gets a new draft, aimed, first. */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { homeTargetAct } from "../desktop/home2/HomeTarget";

describe("aiming the home composer", () => {
  it("aims the draft when the draft is in front", () => {
    expect(homeTargetAct("d1", "d1")).toBe("aim-draft");
  });
  it("opens a new draft when a stored chat is in front", () => {
    expect(homeTargetAct("c-stored", "d1")).toBe("new-draft");
    expect(homeTargetAct("c-stored", undefined)).toBe("new-draft");
  });
  it("opens a new draft when nothing is in front", () => {
    expect(homeTargetAct(undefined, undefined)).toBe("new-draft");
  });
});

describe("the shell", () => {
  const shell = readFileSync(join(__dirname, "..", "MinutesShell.tsx"), "utf8");
  const handler = (() => {
    const at = shell.indexOf("window.addEventListener(HOME_TARGET_EVENT");
    return shell.slice(Math.max(0, shell.lastIndexOf("useEffect(", at)), at);
  })();
  it("asks which act an aim is before re-targeting anything", () => {
    expect(handler).toMatch(/homeTargetAct\(selRef\.current\.chatId, draftRef\.current\?\.id\)/);
    expect(handler).toMatch(/startDraftRef\.current\(wid\)/);
  });
});
