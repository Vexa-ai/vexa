import { describe, expect, it } from "vitest";
import type {
  SharedAttachResult, SwapResult, WorkspaceImport, WorkspaceSlot, activateWorkspace,
} from "../workspaceApi";

/** agent-api's attach, swap, activate and import results carry no `nested` field: the always-false
 *  field was removed on the server, so no type here may promise it. Each `@ts-expect-error` line
 *  fails `tsc` if the field comes back to that type. */
describe("workspace result types carry no nested field", () => {
  it("is absent from every attach, swap, activate and import result", () => {
    const swap = {} as SwapResult;
    const shared = {} as SharedAttachResult;
    const slot = {} as WorkspaceSlot;
    const imported = {} as NonNullable<WorkspaceImport["result"]>;
    const activated = {} as Awaited<ReturnType<typeof activateWorkspace>>;
    // @ts-expect-error SwapResult has no nested
    expect(swap.nested).toBeUndefined();
    // @ts-expect-error SharedAttachResult has no nested
    expect(shared.nested).toBeUndefined();
    // @ts-expect-error WorkspaceSlot has no nested
    expect(slot.nested).toBeUndefined();
    // @ts-expect-error an import result has no nested
    expect(imported.nested).toBeUndefined();
    // @ts-expect-error activateWorkspace's result has no nested
    expect(activated.nested).toBeUndefined();
  });
});
