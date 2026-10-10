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
import { connectionGap, EMPTY_LINE, jtbdProposal, SEND_BOT, type Proposal } from "../proposals";

afterEach(() => cleanup());

const JOB = jtbdProposal({ id: "row-1", source: "meeting:97", source_label: "Pilot sync",
                           act: "The migration doc, by Friday" });
const CONNECT = connectionGap([])!;
const LINK = SEND_BOT;

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

  it("derived acts carry no source and no ×", () => {
    row([CONNECT, LINK]);
    expect(screen.queryByRole("button", { name: /^Dismiss:/ })).toBeNull();
    expect(screen.getByText("Send Vexa to a meeting")).toBeTruthy();
  });

  it("with nothing useful, a short friendly line instead of filler", () => {
    render(<ProposalChips items={[LINK]} line={EMPTY_LINE} onPick={vi.fn()} />);
    expect(screen.getByText(EMPTY_LINE)).toBeTruthy();
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
    row([JOB, CONNECT, LINK]);
    const kinds = Array.from(document.querySelectorAll("[data-proposal]"))
      .map((el) => el.getAttribute("data-proposal"));
    expect(kinds).toEqual(["jtbd", "connect", "link"]);
  });
});

describe("send Vexa to a meeting — the chip opens a paste field", () => {
  const open = () => {
    const onPick = vi.fn(), onSendLink = vi.fn();
    render(<ProposalChips items={[LINK]} onPick={onPick} onSendLink={onSendLink} />);
    fireEvent.click(screen.getByText("Send Vexa to a meeting"));
    return { onPick, onSendLink };
  };

  it("a click opens the field and says nothing to the agent", () => {
    const { onPick, onSendLink } = open();
    expect(screen.getByRole("textbox", { name: "Meeting link" })).toBeTruthy();
    expect(onPick).not.toHaveBeenCalled();
    expect(onSendLink).not.toHaveBeenCalled();
  });

  it("a valid link is sent", async () => {
    const { onSendLink } = open();
    fireEvent.change(screen.getByRole("textbox", { name: "Meeting link" }), { target: { value: "https://meet.google.com/abc-defg-hij" } });
    fireEvent.click(screen.getByRole("button", { name: "Send bot" }));
    await vi.waitFor(() => expect(onSendLink).toHaveBeenCalledWith("https://meet.google.com/abc-defg-hij"));
  });

  it("anything else is refused beside the field, with the input kept", async () => {
    const { onSendLink } = open();
    const box = screen.getByRole("textbox", { name: "Meeting link" }) as HTMLInputElement;
    fireEvent.change(box, { target: { value: "not a link" } });
    fireEvent.click(screen.getByRole("button", { name: "Send bot" }));
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toContain("isn't a Google Meet"));
    expect(onSendLink).not.toHaveBeenCalled();
    expect(box.value).toBe("not a link");
  });

  it("Cancel brings the chips back", () => {
    open();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Send Vexa to a meeting")).toBeTruthy();
  });
});
