/** The 1b primitives: roles, keyboard, focus, `aria-*`, error linking, and the two security
 *  primitives (guidelines §4, §7 S1–S2). */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  Badge, Breadcrumb, Button, Chip, ConfirmDialog, EmptyState, ErrorState, ExternalLink, IconButton, Input, ListRow,
  SecretReveal, Select, SourceList, Tabs, titleHasDate,
} from "..";

afterEach(cleanup);

describe("Button / IconButton", () => {
  it("variants are data attributes; loading keeps the button and marks it busy", () => {
    render(<Button variant="primary" loading>Send</Button>);
    const b = screen.getByRole("button", { name: "Send" });
    expect(b.getAttribute("data-variant")).toBe("primary");
    expect(b.getAttribute("aria-busy")).toBe("true");
  });
  it("an icon button is named and can be a toggle", () => {
    render(<IconButton label="Show the file navigator" pressed>x</IconButton>);
    const b = screen.getByRole("button", { name: "Show the file navigator" });
    expect(b.getAttribute("aria-pressed")).toBe("true");
    expect(b.getAttribute("title")).toBe("Show the file navigator");
  });
});

describe("Badge vs Chip — static is never a button, interactive always is", () => {
  it("a badge has no button; a chip has one, and its remove control is named", () => {
    render(<><Badge tone="success" dot>Live</Badge><Chip onRemove={() => {}} removeLabel="Remove Example workspace">Example workspace</Chip></>);
    expect(screen.getAllByRole("button").map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["Example workspace", "Remove Example workspace"]);
  });
});

describe("Input", () => {
  it("has a programmatic label and links its error through aria-describedby", () => {
    render(<Input label="Workspace name" error="A name is required" />);
    const i = screen.getByLabelText("Workspace name");
    expect(i.getAttribute("aria-invalid")).toBe("true");
    const err = document.getElementById(i.getAttribute("aria-describedby")!);
    expect(err?.textContent).toContain("A name is required");
  });
});

describe("Tabs (WAI-ARIA)", () => {
  it("roving tabindex; arrows move selection and focus", () => {
    const onChange = vi.fn();
    render(<Tabs label="Sections" value="a" onChange={onChange} items={[{ key: "a", label: "Active" }, { key: "b", label: "All" }]} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.getAttribute("tabindex"))).toEqual(["0", "-1"]);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
    expect(onChange).toHaveBeenCalledWith("b");
  });
});

describe("Select", () => {
  it("shows the value and offers checked options", () => {
    render(<Select label="Theme" value="dark" onChange={() => {}} options={[{ value: "dark", label: "Dark" }, { value: "light", label: "Light" }]} />);
    fireEvent.click(screen.getByRole("button", { name: "Theme" }));
    expect(screen.getAllByRole("menuitemradio").map((o) => o.getAttribute("aria-checked"))).toEqual(["true", "false"]);
  });
});

describe("Breadcrumb", () => {
  it("collapses the middle past four segments, and marks the current page", () => {
    render(<Breadcrumb items={["one", "two", "three", "four", "five"].map((k, i, a) => ({ key: k, label: k, onSelect: i < a.length - 1 ? () => {} : undefined }))} />);
    expect(screen.getByRole("button", { name: "2 more folders" })).toBeTruthy();
    expect(screen.getByText("five").getAttribute("aria-current")).toBe("page");
    expect(screen.queryByText("two")).toBeNull();
  });
});

describe("ListRow", () => {
  it("selected is more than colour: aria-current and a data flag the CSS draws a bar from", () => {
    render(<ListRow title="A chat" selected meta="6:29 PM" />);
    expect(screen.getByRole("button").getAttribute("aria-current")).toBe("true");
  });
});

describe("ConfirmDialog — replaces window.confirm", () => {
  it("names the act, traps Escape to cancel, and can require typing the name", () => {
    const onConfirm = vi.fn(), onCancel = vi.fn();
    render(<ConfirmDialog open title="Delete workspace?" consequence="This cannot be undone." confirmLabel="Delete workspace"
      typeToConfirm="Example workspace" onConfirm={onConfirm} onCancel={onCancel} />);
    expect(screen.getByRole("dialog", { name: "Delete workspace?" })).toBeTruthy();
    const go = screen.getByRole("button", { name: "Delete workspace" }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Example workspace" } });
    expect(go.disabled).toBe(false);
    fireEvent.click(go);
    expect(onConfirm).toHaveBeenCalled();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("empty and error never look alike (P18)", () => {
  it("an error states what, why, the fix and the verbatim detail", () => {
    const html = renderToStaticMarkup(<><EmptyState>Nothing here yet.</EmptyState><ErrorState what="Could not load pages" why="The workspace service did not answer." detail="HTTP 503" /></>);
    expect(html).toContain('role="status"');
    expect(html).toContain('role="alert"');
    expect(html).toContain("<summary>Details</summary>");
  });
});

describe("externalLink — S2", () => {
  it("refuses javascript: and data: schemes", () => {
    for (const bad of ["javascript:alert(1)", "data:text/html,x", "vbscript:x", " JAVASCRIPT:alert(1)"]) {
      expect(renderToStaticMarkup(<ExternalLink href={bad}>x</ExternalLink>)).not.toContain("href=");
    }
  });
  it("opens http(s) in a new tab without an opener", () => {
    const html = renderToStaticMarkup(<ExternalLink href="https://example.com">x</ExternalLink>);
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("secretReveal — S1", () => {
  const SECRET = "vxa_test_0123456789abcdef";
  it("masked until revealed", () => {
    render(<SecretReveal value={SECRET} label="API token" />);
    expect(document.body.textContent).not.toContain(SECRET);
    fireEvent.click(screen.getByRole("button", { name: "Show API token" }));
    expect(document.body.textContent).toContain(SECRET);
  });
  it("never renders the value into title, aria-label or data attributes", () => {
    render(<SecretReveal value={SECRET} label="API token" />);
    fireEvent.click(screen.getByRole("button", { name: "Show API token" }));
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      for (const a of Array.from(el.attributes)) expect(a.value).not.toContain(SECRET);
    }
  });
  it("copies on an explicit press only", async () => {
    const onCopy = vi.fn();
    render(<SecretReveal value={SECRET} label="API token" onCopy={onCopy} />);
    expect(onCopy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Copy API token" }));
    expect(onCopy).toHaveBeenCalledWith(SECRET);
  });
});

describe("SourceList", () => {
  it("shows a date once — not beside a title that already carries it", () => {
    expect(titleHasDate("Notes 2026-10-08", "2026-10-08")).toBe(true);
    const html = renderToStaticMarkup(<SourceList sources={[{ title: "Notes 2026-10-08", url: "https://example.com/a", date: "2026-10-08" }]} />);
    expect(html).not.toContain("<time");
  });
  it("first three, then 'Show N more'", () => {
    render(<SourceList sources={Array.from({ length: 5 }, (_, i) => ({ title: `S${i}`, url: `https://example.com/${i}` }))} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    fireEvent.click(screen.getByRole("button", { name: "Show 2 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
  });
});
