/** A RETIRED ROUTINE'S CHAT IS A NORMAL CHAT (founder, live app.dev: "a retired routine should not
 *  have the routine badge"). The rail reads the routines' own state: active → the routine icon and
 *  the Routines section; paused → a paused icon; retired or gone → no icon, back in Recent. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup } from "@testing-library/react";
import { withRoutineState } from "../chats";
import { folderOf, ROUTINES, UNGROUPED } from "../railFolders";

afterEach(() => cleanup());

const row = (flow: string) => ({ key: `c:${flow}`, routine: { flow, scope: "user:2", version: 1 } });
const folders = { folders: [{ id: ROUTINES, name: "Routines", builtin: true, collapsed: false }, { id: UNGROUPED, name: "Recent", builtin: true, collapsed: false }], filings: {}, order: {} } as never;

describe("a routine chat's badge follows the routine", () => {
  const routines = [{ name: "active-one", enabled: true, status: "active" }, { name: "paused-one", enabled: false, status: "paused" },
    { name: "retired-one", enabled: false, status: "retired" }];

  it("active keeps the badge and the Routines section", () => {
    const [r] = withRoutineState([row("active-one")], routines);
    expect(r.routine?.flow).toBe("active-one");
    expect(r.routinePaused).toBeFalsy();
    expect(folderOf(r, folders)).toBe(ROUTINES);
  });

  it("paused keeps it, marked paused", () => {
    const [r] = withRoutineState([row("paused-one")], routines);
    expect(r.routine?.flow).toBe("paused-one");
    expect(r.routinePaused).toBe(true);
  });

  it("retired — or no longer listed — is a normal chat: no badge, back in Recent", () => {
    for (const flow of ["retired-one", "gone-one"]) {
      const [r] = withRoutineState([row(flow)], routines);
      expect(r.routine).toBeUndefined();
      expect(folderOf(r, folders)).toBe(UNGROUPED);
    }
  });

  it("before the routines answer, nothing changes", () => {
    expect(withRoutineState([row("gone-one")], null)[0].routine?.flow).toBe("gone-one");
  });
});
