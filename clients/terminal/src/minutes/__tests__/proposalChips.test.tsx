/** The chip row itself (Vexa-ai/vexa#1614) — what it renders, and the two things a click can mean.
 *
 *  `proposals.test.ts` proves WHAT is offered. This proves the row: an item another agent wrote
 *  shows its source and can be refused; a derived or standing chip cannot (there is nothing to
 *  refuse — it is a statement about this account, not a job somebody filed for you); and picking and
 *  dismissing are two different verbs, because only one of them is feedback about the proposal.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ProposalChips } from "../ProposalChips";
import { jtbdProposal, standingProposals, type Proposal } from "../proposals";

afterEach(() => cleanup());

const JOB = jtbdProposal({ id: "row-1", source: "meeting:97", source_label: "Pilot sync",
                           act: "The migration doc, by Friday" });
const [MEET, LINK] = standingProposals(false);

const row = (items: Proposal[], onPick = vi.fn(), onDismiss = vi.fn()) => {
  render(<ProposalChips items={items} onPick={onPick} onDismiss={onDismiss} />);
  return { onPick, onDismiss };
};

describe("what the row shows", () => {
  it("nothing at all when there is nothing to offer", () => {
    render(<ProposalChips items={[]} onPick={vi.fn()} />);
    expect(screen.queryByRole("group")).toBeNull();
  });

  it("an agent-written item says its act AND where it came from", () => {
    row([JOB]);
    const chip = screen.getByText(/The migration doc, by Friday/);
    expect(chip.textContent).toContain("The migration doc, by Friday");
    expect(chip.textContent).toContain("Pilot sync");
  });

  it("the standing acts carry no source and no ×", () => {
    row([MEET, LINK]);
    expect(screen.queryByRole("button", { name: /^Dismiss:/ })).toBeNull();
    expect(screen.getByText("Paste a meeting link")).toBeTruthy();
  });
});

describe("picking and dismissing are two different verbs", () => {
  it("a click fires the act — the chip hands back the whole proposal, kick and all", () => {
    const { onPick, onDismiss } = row([JOB]);
    fireEvent.click(screen.getByText(/The migration doc, by Friday/));
    expect(onPick).toHaveBeenCalledWith(JOB);
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("the × dismisses THAT item and fires nothing", () => {
    const { onPick, onDismiss } = row([JOB]);
    fireEvent.click(screen.getByRole("button", { name: "Dismiss: The migration doc, by Friday" }));
    expect(onDismiss).toHaveBeenCalledWith(JOB);
    expect(onPick).not.toHaveBeenCalled();
  });

  it("with no dismiss handler the item is still offered — just not refusable", () => {
    const onPick = vi.fn();
    render(<ProposalChips items={[JOB]} onPick={onPick} />);
    expect(screen.queryByRole("button", { name: /^Dismiss:/ })).toBeNull();
    fireEvent.click(screen.getByText(/The migration doc, by Friday/));
    expect(onPick).toHaveBeenCalledWith(JOB);
  });

  it("every chip is tagged with its kind, so the row is readable from the DOM", () => {
    row([JOB, MEET, LINK]);
    const kinds = Array.from(document.querySelectorAll("[data-proposal]"))
      .map((el) => el.getAttribute("data-proposal"));
    expect(kinds).toEqual(["jtbd", "meet", "link"]);
  });
});
