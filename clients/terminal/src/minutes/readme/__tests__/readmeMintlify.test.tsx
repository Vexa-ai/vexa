/** The workspace page draws the Mintlify dialect agents write (founder, 2026-10-18: "things do not
 *  render" — `<Steps>`, `<Step title=…>`, `</Warning>` and `[x]` were printed as text). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ReadmeDoc, readmeSource } from "../ReadmeView";
import { PILOT_TREE, PILOT_TREE_WARNING } from "./pilotPage.fixture";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const RAW = /<\/?(Steps|Step|Warning|Note|Columns|Column)\b|:::|\[[ xX]\]|VXSLOT/;

describe("ReadmeDoc — the pilot page", () => {
  it("draws steps, callout, columns and task boxes; no tag, directive or bracket is printed", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    const { container } = render(<ReadmeDoc tree={PILOT_TREE} hideTitle />);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(RAW);
    // three numbered steps, each with its title
    expect(container.querySelectorAll(".vx-doc-step")).toHaveLength(3);
    expect(screen.getByText("1 · Hand over the channel credential — Vexa → Acme")).toBeTruthy();
    expect(screen.getByText("3 · Publish the release — Vexa")).toBeTruthy();
    // :::callout is a callout; the empty :::columns (its blocks rendered nothing) leaves nothing
    expect(container.querySelectorAll(".vx-doc-callout")).toHaveLength(1);
    expect(container.querySelector(".vx-doc-columns")).toBeNull();
    // the tasks are boxes, three done and four open, read-only here
    expect(screen.getAllByRole("img", { name: /^Done: / })).toHaveLength(3);
    expect(screen.getAllByRole("img", { name: /^Open: / })).toHaveLength(4);
    expect(container.querySelector("input[type=checkbox]")).toBeNull();
    // inline code, links and the table still render; the desk's HTML comments do not
    expect([...container.querySelectorAll("code")].map((c) => c.textContent)).toContain("claim.sh");
    expect(container.querySelector("table")).toBeTruthy();
    expect(text).not.toMatch(/desk:pinned/);
    expect(fetchSpy).not.toHaveBeenCalled();          // nothing ran on render
  });

  it("an earlier revision: <Warning> is a callout, and a live line inside an open <Step> stays in it", () => {
    const { container } = render(<ReadmeDoc tree={PILOT_TREE_WARNING} />);
    expect(container.textContent).not.toMatch(RAW);
    const callout = container.querySelector(".vx-doc-callout");
    expect(callout?.getAttribute("data-tone")).toBe("warning");
    expect(callout?.textContent).toMatch(/Where we are:/);
    // the server's line node (a task with a typed reference) is drawn inside step 1, as a box + chip
    const steps = container.querySelectorAll(".vx-doc-step");
    expect(steps).toHaveLength(1);
    const line = steps[0].querySelector(".vx-rd-line[data-task=open]");
    expect(line).toBeTruthy();
    expect(line?.textContent).toMatch(/Revoke the first credential with Mira Example \(oc only\)/);
    expect(line?.querySelector("code")?.textContent).toBe("oc");
    expect(line?.querySelector("[data-ref='person:7']")).toBeTruthy();
  });

  it("joins the server's stretches back into one source with a slot per live part", () => {
    const { source, slots } = readmeSource(PILOT_TREE_WARNING);
    expect(slots).toHaveLength(1);
    expect(source).toMatch(/<Step title="1[^"]*">[\s\S]*VXSLOT-0-VXSLOT[\s\S]*<\/Step>/);
  });
});
