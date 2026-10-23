/** Founder 2026-10-16: a commitments page printed `<Steps>`, `<Step title="…">` and `</Steps>` as
 *  literal text. Steps render as a vertical timeline; an unknown tag degrades to its children with
 *  its title as a bold line; no literal tag is ever shown. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { RichPage } from "../desktop/home2/RichPage";
import { degradeUnknownComponents } from "../../ui-kit/MdxDoc";

vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
afterEach(cleanup);

const PAGE = [
  "# Commitments",
  "",
  "<Steps>",
  '<Step title="By Fri 16 Oct: confirm the venue">',
  "Ana checks with [[person:ana|Ana]] and books it.",
  "</Step>",
  '<Step title="By Mon 19 Oct: send the invites">',
  "Draft in the room.",
  "</Step>",
  "</Steps>",
  "",
  '<Timeline title="Later">',
  "After the event.",
  "</Timeline>",
].join("\n");

describe("components on a rich page", () => {
  it("draws Steps as a timeline and never prints a tag", async () => {
    const { container } = render(<RichPage slug="dna" path="commitments.md" md={PAGE} />);
    await new Promise((r) => setTimeout(r, 0));
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/<\/?(Steps|Step|Timeline)/);
    expect(container.querySelectorAll("[data-rp-step]")).toHaveLength(2);
    expect(text).toContain("By Fri 16 Oct: confirm the venue");
    expect(text).toContain("Draft in the room.");
    expect(text).toContain("Later");
    expect(text).toContain("After the event.");
  });
  it("an unknown component keeps its children and its title, never the tag", () => {
    expect(degradeUnknownComponents('<Timeline title="Later">\nAfter.\n</Timeline>')).toBe("\n**Later**\n\nAfter.\n");
    expect(degradeUnknownComponents("<Steps>\n<Step title=\"a\">x</Step>\n</Steps>")).toBe("<Steps>\n<Step title=\"a\">x</Step>\n</Steps>");
    expect(degradeUnknownComponents("a <meeting_id> b")).toBe("a <meeting_id> b");
  });
});
