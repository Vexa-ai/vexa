/** "Listening for: …" on the meeting page — the brief people can see and change. Hidden when signals
 *  are off or there is no brief; a click opens a plain editor; Save writes through the page. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { setFeatureOverride } from "../../platform/features";
import { ListenLine } from "../ListenLine";
import { readListenBrief } from "../listenBrief";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PAGE = [
  "---", "title: Install review", "listen:", "  focus: Install blockers and dates", "  labels:",
  "    - {name: blocker, wake: true}", "    - {name: decision, wake: true}", "    - {name: addressed_to_vexa, wake: true}",
  "    - {name: new_name, wake: false}", "  version: 2", "---", "", "# Install review", "",
].join("\n");

let container: HTMLDivElement; let root: Root;
beforeEach(() => { container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(() => { act(() => root.unmount()); container.remove(); setFeatureOverride("all"); });
const render = (ui: React.ReactElement) => act(() => { root.render(ui); });

describe("ListenLine", () => {
  it("shows what Vexa listens for, compactly, with the wake labels", () => {
    render(<ListenLine raw={PAGE} onSave={vi.fn()} />);
    const line = container.querySelector("[data-listen-line]")!;
    expect(line.textContent).toContain("Listening for");
    expect(line.textContent).toContain("Install blockers and dates");
    expect([...line.querySelectorAll("[data-listen-label]")].map((x) => x.textContent)).toEqual(["Blocker", "Decision", "To Vexa"]);
  });
  it("is hidden when signals are switched off", () => {
    setFeatureOverride({ transcript_signals: false });
    render(<ListenLine raw={PAGE} onSave={vi.fn()} />);
    expect(container.querySelector("[data-listen-line]")).toBeNull();
  });
  it("is hidden when the page has no brief", () => {
    render(<ListenLine raw={"---\ntitle: x\n---\n# x\n"} onSave={vi.fn()} />);
    expect(container.querySelector("[data-listen-line]")).toBeNull();
  });
  it("opens a plain editor and saves the edited brief through the page", async () => {
    const onSave = vi.fn(async (_next: string) => {});
    render(<ListenLine raw={PAGE} onSave={onSave} />);
    act(() => { (container.querySelector("[data-listen-line]") as HTMLElement).click(); });
    const focus = container.querySelector("[data-listen-focus]") as HTMLTextAreaElement;
    expect(focus.value).toBe("Install blockers and dates");
    act(() => {
      const set = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
      set.call(focus, "Dates only");
      focus.dispatchEvent(new Event("input", { bubbles: true }));
    });
    // the always-on label cannot be removed
    expect(container.querySelector('[data-listen-row="addressed_to_vexa"] [data-listen-remove]')).toBeNull();
    await act(async () => { (container.querySelector("[data-listen-save]") as HTMLElement).click(); });
    expect(onSave).toHaveBeenCalledTimes(1);
    const saved = readListenBrief(onSave.mock.calls[0][0])!;
    expect(saved.focus).toBe("Dates only");
    expect(saved.version).toBe(3);
    expect(container.querySelector("[data-listen-editor]")).toBeNull();
  });
  it("a read-only page shows the line but offers no editor", () => {
    render(<ListenLine raw={PAGE} readonly onSave={vi.fn()} />);
    const line = container.querySelector("[data-listen-line]") as HTMLElement;
    expect(line).not.toBeNull();
    act(() => { line.click(); });
    expect(container.querySelector("[data-listen-editor]")).toBeNull();
  });
});
