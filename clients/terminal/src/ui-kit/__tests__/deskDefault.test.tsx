import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, it, expect } from "vitest";
import { useRef } from "react";
import { useShellLayout, readShellStore, SHELL_KEY } from "../layout/useShellLayout";

// THE DESK IS OFF BY DEFAULT (founder 2026-10-13: "let's make the right side panel off by default
// because this desk is not actually helpful now"). It opens when the reader asks for a page.
function Probe() {
  const root = useRef<HTMLDivElement>(null);
  const s = useShellLayout(root, { storageKey: "desk-test" });
  return <div ref={root}>
    <span data-testid="pages">{s.layout.pagesKind}:{String(s.prefs.pagesOpen)}</span>
    <button onClick={() => s.revealPages(false)}>agent wrote</button>
    <button onClick={() => s.revealPages(true)}>reader asked</button>
    <button onClick={() => s.setPagesOpen(false)}>close</button>
  </div>;
}

beforeEach(() => { localStorage.clear(); Object.defineProperty(window, "innerWidth", { value: 1600, configurable: true }); });
afterEach(cleanup);

describe("the desk on a wide screen", () => {
  it("starts closed; a page the agent wrote does not open it; the reader's ask does, and is remembered", () => {
    render(<Probe />);
    expect(screen.getByTestId("pages").textContent).toBe("collapsed:false");
    fireEvent.click(screen.getByText("agent wrote"));
    expect(screen.getByTestId("pages").textContent).toBe("collapsed:false");
    fireEvent.click(screen.getByText("reader asked"));
    expect(screen.getByTestId("pages").textContent).toBe("docked:true");
    expect(readShellStore(undefined, "desk-test").prefs).toMatchObject({ pagesOpen: true, pagesChosen: true });
    fireEvent.click(screen.getByText("close"));
    expect(readShellStore(undefined, "desk-test").prefs).toMatchObject({ pagesOpen: false, pagesChosen: true });
  });
  it("the default store key is unchanged", () => { expect(SHELL_KEY).toBe("vexa.shell.v1"); });
});
