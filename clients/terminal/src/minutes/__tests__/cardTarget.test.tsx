/** A workspace card click SETS THE TARGET (UI decisions; founder bug 2026-10-16: the old home opened
 *  the README but the composer still said Personal). Clicking the selected card again resets it. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render } from "@testing-library/react";
vi.mock("../../ui-kit/hoverCard", () => ({ useHoverCard: () => ({}) }));
import { HOME_TARGET_EVENT } from "../../platform/events";
import { WorkspaceTile } from "../desktop/DesktopTiles";
import { resetHomeTarget, toggleHomeTarget } from "../desktop/home2/HomeTarget";

const heard: string[] = [];
const on = (e: Event) => heard.push(String((e as CustomEvent<{ workspace?: string }>).detail?.workspace ?? ""));
window.addEventListener(HOME_TARGET_EVENT, on);
vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 })));

afterEach(() => { cleanup(); heard.length = 0; resetHomeTarget(); });

const w = { kind: "workspace", ref: "w:citz", slug: "citz-1", name: "Citizenship applications", write: true, members: [], counts: {} } as never;
const drag = { draggable: false } as never;

describe("a card click sets the target", () => {
  it("toggles: first click aims the workspace, the second resets to Personal", () => {
    toggleHomeTarget("citz-1");
    expect(heard).toEqual(["citz-1"]);
    toggleHomeTarget("citz-1");
    expect(heard).toEqual(["citz-1", ""]);
  });
  it("the old home's tile aims its workspace on click, and resets on a second click", () => {
    const { container } = render(<WorkspaceTile w={w} drag={drag} />);
    const tile = container.querySelector("[data-workspace-tile]") as HTMLElement;
    fireEvent.click(tile);
    expect(heard).toEqual(["citz-1"]);
    fireEvent.click(tile);
    expect(heard).toEqual(["citz-1", ""]);
  });
  it("a read-only workspace is never aimed", () => {
    const { container } = render(<WorkspaceTile w={{ ...(w as object), write: false } as never} drag={drag} />);
    fireEvent.click(container.querySelector("[data-workspace-tile]") as HTMLElement);
    expect(heard).toEqual([]);
  });
});
