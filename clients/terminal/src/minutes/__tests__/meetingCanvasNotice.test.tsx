/** A meeting in front renders the meeting view even when a notice from an earlier page is still
 *  held. The notice belongs to a link that would not open; once the reader is on a meeting, the
 *  panel showed that sentence (or nothing) instead of the live transcript, and the view never
 *  mounted, so it never fetched the transcript or subscribed to the live stream.
 */
import { describe, it, expect, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { PagesPanel } from "../PagesPanel";
import { registry } from "../../contributions";

afterEach(() => { cleanup(); });

registry.registerTab("meeting", ({ params }) => <div data-testid="meeting-canvas">{String(params.meetingId)}</div>);

describe("meeting view and a held notice", () => {
  it("renders the meeting canvas when a meeting is in front, even with a notice held", () => {
    const { getByTestId, container } = render(
      <PagesPanel pages={[{ kind: "meeting", path: "100", label: "Google Meet · abc-defg-hij" }]}
        docPath="100" docKind="meeting" onOpen={() => {}} body={null}
        notice="You do not have access to that workspace." />,
    );
    expect(getByTestId("meeting-canvas").textContent).toBe("100");
    expect(container.querySelector("[data-pages-notice]")).toBeNull();
  });

  it("still shows the notice for a document in front", () => {
    const { container } = render(
      <PagesPanel pages={[{ path: "README.md", label: "README" }]}
        docPath="README.md" onOpen={() => {}} body={null}
        notice="You do not have access to that workspace." />,
    );
    expect(container.querySelector("[data-pages-notice]")?.textContent).toContain("access");
  });
});
