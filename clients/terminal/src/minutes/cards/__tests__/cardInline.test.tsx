/** 7q inline: a ```card fence draws the card; the next actor decides it in place and it folds to
 *  one line; everyone else reads who it waits on. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MdxDoc } from "../../../ui-kit/MdxDoc";
import { provideCardInline } from "../../../ui-kit/shellProvided";
import { resetPeople } from "../../desktop/cardsApi";
import { resetInbox } from "../api";
import { CardInline, parseCardFence } from "../CardInline";
import { card, flush, NOW, PEOPLE, routeFetch } from "./fixtures";

afterEach(() => { cleanup(); resetPeople(); resetInbox(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("CardInline", () => {
  it("parses the fence", () => {
    expect(parseCardFence('{"card":"c-1"}')).toBe("c-1");
    expect(parseCardFence('{"card":"bad id!"}')).toBeNull();
    expect(parseCardFence("nope")).toBeNull();
  });

  it("folds to one line after the next actor decides", async () => {
    vi.spyOn(Date, "now").mockReturnValue(NOW);
    const decided = card({ revision: 4, open: false, viewer: { role: "next_actor", seen: true, actions: [] }, last_decision: { action: "confirm", by: "p-ana", at: new Date(NOW - 3600_000).toISOString() } });
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: card({ viewer: { role: "next_actor", seen: true, actions: ["confirm", "decline"] } }) } }),
      "/card/nonce": () => ({ body: { nonce: "n", content_hash: "h", revision: 3 } }),
      "/card/decide": () => ({ body: { status: "saved", card: decided, decision: {} } }), "/card/inbox": () => ({ body: {} }) });
    render(<CardInline source='{"card":"c-1"}' />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm" })).toBeTruthy());
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(document.querySelector("[data-card-inline=c-1][data-folded]")).toBeTruthy());
    await waitFor(() => expect(screen.getByText(/^Signed by Ana · /)).toBeTruthy());
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("a viewer who is not the next actor reads 'Waiting on …', with no buttons", async () => {
    vi.spyOn(Date, "now").mockReturnValue(NOW);
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: card({ next_actor: "p-val", viewer: { role: "requester", seen: true, actions: [] } }) } }) });
    render(<CardInline source='{"card":"c-1"}' />);
    await waitFor(() => expect(screen.getByText("Waiting on Valeriia · 8 h")).toBeTruthy());
    expect(document.querySelector("[data-decision-block]")).toBeNull();
  });

  it("the preview shows ~6 lines with 'show all'", async () => {
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: card({ summary: "1\n2\n3\n4\n5\n6\n7\n8", viewer: { role: "watcher", seen: true, actions: [] } }) } }) });
    render(<CardInline source='{"card":"c-1"}' />);
    await waitFor(() => expect(screen.getByRole("button", { name: "show all · 8 lines" })).toBeTruthy());
    expect(document.querySelector(".vx-ci-pre")!.textContent).toBe("1\n2\n3\n4\n5\n6");
  });

  it("a ```card fence in a reply draws it", async () => {
    routeFetch({ ...PEOPLE, "/card/read": () => ({ body: { card: card({ viewer: { role: "watcher", seen: true, actions: [] } }) } }) });
    provideCardInline(CardInline);   // the composition root supplies it in the app (app/App.tsx)
    render(<MdxDoc>{"Here it is.\n\n```card\n{\"card\":\"c-1\"}\n```"}</MdxDoc>);
    await waitFor(() => expect(document.querySelector("[data-card-inline=c-1]")).toBeTruthy());
    await flush();
  });
});
