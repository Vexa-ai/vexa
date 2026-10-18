/** The chip row itself — what it renders, and the two things a click can mean.
 *
 *  `proposals.test.ts` proves WHAT is offered. This proves the row: every chip is a real button a
 *  keyboard reaches; an item another agent wrote shows its source and can be refused; a connection
 *  chip cannot (it is a statement about this account, not a job somebody filed); and picking and
 *  dismissing are two different verbs. */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ProposalChips } from "../ProposalChips";
import { CHECK_EMAIL, jtbdProposal, prepProposal, proposals, setupProposal, CALL_VEXA, type Proposal } from "../proposals";

afterEach(() => cleanup());

const JOB = jtbdProposal({ id: "row-1", source: "meeting:97", source_label: "Pilot sync",
                           act: "The migration doc, by Friday" });
const [CONNECT_EMAIL, CONNECT_CAL] = proposals([], null);

const row = (items: Proposal[], onPick = vi.fn(), onDismiss = vi.fn()) => {
  render(<ProposalChips items={items} onPick={onPick} onDismiss={onDismiss} />);
  return { onPick, onDismiss };
};

describe("what the row shows", () => {
  it("nothing at all when there is nothing to offer", () => {
    render(<ProposalChips items={[]} onPick={vi.fn()} />);
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("the connection chips, as buttons in a labelled group", () => {
    row([CONNECT_EMAIL, CONNECT_CAL]);
    expect(screen.getByRole("group", { name: "Suggestions" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect your email" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Connect your calendar" })).toBeTruthy();
  });

  it("an agent-written item says its act AND where it came from", () => {
    row([JOB]);
    const chip = screen.getByText(/The migration doc, by Friday/);
    expect(chip.textContent).toContain("Pilot sync");
  });

  it("connection chips carry no source and no ×", () => {
    row([CONNECT_EMAIL, CHECK_EMAIL]);
    expect(screen.queryByRole("button", { name: /^Dismiss:/ })).toBeNull();
  });

  it("a long label is cut with an ellipsis and kept whole in the tooltip (phone width)", () => {
    const long = { ...CHECK_EMAIL, label: "Prepare for " + "Quarterly business review ".repeat(6) + "at 3:00 PM" };
    row([long]);
    const b = screen.getByRole("button", { name: long.label });
    expect(b.getAttribute("title")).toBe(long.label);
    expect(b.style.maxWidth).toBe("100%");
    // the label is cut, never the icon in front of it
    const label = b.querySelector("[data-proposal-label]") as HTMLElement;
    expect(label.style.textOverflow).toBe("ellipsis");
    expect(label.style.minWidth).toBe("0px");
  });
});

describe("every chip leads with an icon (founder 2026-10-13: \"hints should have icons\")", () => {
  const PREP = prepProposal({ title: "Design sync", at: Date.now() + 3_600_000 });
  const ALL: Proposal[] = [PREP, CHECK_EMAIL, CONNECT_EMAIL, CALL_VEXA, JOB, setupProposal("a@b.c")];
  const icon = (kind: string) => document.querySelector(`[data-proposal="${kind}"] .vx-chip-icon svg`);

  it("one per chip, before the label, hidden from the accessible name", () => {
    row(ALL);
    for (const b of Array.from(document.querySelectorAll("[data-proposal]"))) {
      const first = b.firstElementChild as HTMLElement;
      expect(first.classList.contains("vx-chip-icon")).toBe(true);
      expect(first.getAttribute("aria-hidden")).toBe("true");
      expect(first.querySelector("svg")).toBeTruthy();
    }
    expect(screen.getByRole("button", { name: "Check my email" })).toBeTruthy();
  });

  it("the icon names the act: calendar for prep, mail for email, plug for connect, a phone for a call with Vexa", () => {
    row(ALL);
    expect(icon("prep")?.classList.contains("lucide-calendar-clock")).toBe(true);
    expect(icon("check-email")?.classList.contains("lucide-mail")).toBe(true);
    expect(icon("connect")?.classList.contains("lucide-plug")).toBe(true);
    expect(icon("call")?.classList.contains("lucide-phone")).toBe(true);
    expect(icon("jtbd")).toBeTruthy();
    expect(icon("setup")).toBeTruthy();
  });

  it("one size and one stroke, the entity chips' (14px, 1.75)", () => {
    row(ALL);
    const svgs = Array.from(document.querySelectorAll("[data-proposal] .vx-chip-icon svg"));
    expect(svgs).toHaveLength(ALL.length);
    for (const s of svgs) {
      expect(s.getAttribute("width")).toBe("14");
      expect(s.getAttribute("stroke-width")).toBe("1.75");
    }
  });
});

describe("keyboard", () => {
  it("every chip is a native button of type=button — Tab reaches it, Enter and Space press it", () => {
    row([CONNECT_EMAIL, CHECK_EMAIL, JOB]);
    for (const b of Array.from(document.querySelectorAll("[data-proposal]"))) {
      expect(b.tagName).toBe("BUTTON");
      expect(b.getAttribute("type")).toBe("button");
      expect(b.getAttribute("tabindex")).not.toBe("-1");
    }
  });

  it("focus highlights the chip the way hover does", () => {
    row([CHECK_EMAIL]);
    const b = screen.getByRole("button", { name: "Check my email" });
    const before = b.style.color;
    fireEvent.focus(b);
    expect(b.style.color).not.toBe(before);
    fireEvent.blur(b);
    expect(b.style.color).toBe(before);
  });
});

describe("picking and dismissing are two different verbs", () => {
  it("a click hands back the whole proposal", () => {
    const { onPick } = row([CHECK_EMAIL, CONNECT_CAL]);
    fireEvent.click(screen.getByRole("button", { name: "Check my email" }));
    expect(onPick).toHaveBeenCalledWith(CHECK_EMAIL);
    fireEvent.click(screen.getByRole("button", { name: "Connect your calendar" }));
    expect(onPick).toHaveBeenCalledWith(CONNECT_CAL);
  });

  it("the × dismisses THAT item and fires nothing", () => {
    const { onPick, onDismiss } = row([JOB]);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss: The migration doc, by Friday" }));
    expect(onDismiss).toHaveBeenCalledWith(JOB);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("every chip is tagged with its kind, so the row is readable from the DOM", () => {
    row([CHECK_EMAIL, JOB, CONNECT_CAL]);
    const kinds = Array.from(document.querySelectorAll("[data-proposal]")).map((el) => el.getAttribute("data-proposal"));
    expect(kinds).toEqual(["check-email", "jtbd", "connect"]);
  });
});
