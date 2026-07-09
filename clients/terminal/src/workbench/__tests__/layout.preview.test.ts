/** LayoutService PREVIEW/PIN transitions — VS Code preview-tab semantics.
 *
 *  Single-click → openPreview: one shared slot, REPLACED in place on the next single-click.
 *  Double-click → openTab: persistent, accumulates.
 *  Pinning the thing currently in preview promotes it (clears the slot).
 *
 *  Drives the service against a minimal fake DockviewApi that records addPanel/updateParameters/
 *  setTitle/close — enough to assert the observable behaviour without a real DOM/dockview.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { createLayoutService, type LayoutService, type TabDescriptor } from "../layout";

interface FakePanel {
  id: string;
  title: string;
  params: Record<string, unknown>;
  active: boolean;
  removeListeners: ((p: { id: string }) => void)[];
}

function fakeApi(svc: LayoutService) {
  const panels = new Map<string, FakePanel>();
  const removeListeners: ((p: { id: string }) => void)[] = [];
  const mkPanelApi = (p: FakePanel) => ({
    setActive: () => { for (const x of panels.values()) x.active = false; p.active = true; },
    setTitle: (t: string) => { p.title = t; },
    updateParameters: (params: Record<string, unknown>) => { p.params = { ...params }; },
    close: () => {
      panels.delete(p.id);
      for (const l of removeListeners) l({ id: p.id });
    },
  });
  const api = {
    panels: [] as unknown[],
    getPanel: (id: string) => { const p = panels.get(id); return p ? { id: p.id, api: mkPanelApi(p) } : undefined; },
    addPanel: (d: { id: string; title: string; params: Record<string, unknown> }) => {
      const p: FakePanel = { id: d.id, title: d.title, params: { ...d.params }, active: false, removeListeners };
      panels.set(d.id, p);
      for (const x of panels.values()) x.active = false;
      p.active = true;
    },
    onDidActivePanelChange: () => ({ dispose() {} }),
    onDidRemovePanel: (fn: (p: { id: string }) => void) => { removeListeners.push(fn); return { dispose() {} }; },
    onDidLayoutChange: () => ({ dispose() {} }),
    toJSON: () => ({}),
    fromJSON: () => {},
    clear: () => panels.clear(),
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  svc.attach(api as any);
  return { api, panels };
}

const doc = (path: string): TabDescriptor => ({ id: `doc:${path}`, title: path, kind: "doc", params: { path }, context: null });

describe("LayoutService preview/pin", () => {
  let svc: LayoutService;
  let panels: Map<string, FakePanel>;
  beforeEach(() => {
    localStorage.clear();
    svc = createLayoutService("files");
    ({ panels } = fakeApi(svc));
  });

  it("single-click reuses ONE preview tab (replaced in place)", () => {
    svc.openPreview(doc("a.md"));
    expect(panels.size).toBe(1);
    const preview = [...panels.values()][0];
    expect(preview.params.preview).toBe(true);
    expect(preview.params.p).toEqual({ path: "a.md" });

    svc.openPreview(doc("b.md"));
    expect(panels.size).toBe(1);              // still ONE tab
    const same = [...panels.values()][0];
    expect(same).toBe(preview);               // same panel instance, swapped content
    expect(same.title).toBe("b.md");
    expect(same.params.p).toEqual({ path: "b.md" });
  });

  it("double-click accumulates persistent pinned tabs", () => {
    svc.openTab(doc("a.md"));
    svc.openTab(doc("b.md"));
    expect(panels.size).toBe(2);
    for (const p of panels.values()) expect(p.params.preview).toBe(false);
  });

  it("pinning the previewed item promotes it and frees the preview slot", () => {
    svc.openPreview(doc("a.md"));
    expect([...panels.keys()]).toEqual(["__preview__"]);

    svc.openTab(doc("a.md"));                  // double-click the same file
    expect(panels.has("__preview__")).toBe(false);   // slot cleared
    expect(panels.has("doc:a.md")).toBe(true);        // now a persistent panel
    expect(panels.get("doc:a.md")!.params.preview).toBe(false);

    // a fresh single-click opens a NEW preview, not touching the pinned one
    svc.openPreview(doc("b.md"));
    expect(panels.has("doc:a.md")).toBe(true);
    expect(panels.has("__preview__")).toBe(true);
    expect(panels.size).toBe(2);
  });

  it("single-click on an already-pinned tab just activates it (no preview dupe)", () => {
    svc.openTab(doc("a.md"));
    svc.openPreview(doc("a.md"));
    expect(panels.size).toBe(1);
    expect(panels.has("__preview__")).toBe(false);
    expect(panels.get("doc:a.md")!.active).toBe(true);
  });
});
