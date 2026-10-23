/** D35 (PRD 0.13.4 S3): the picker lists a Person once with "2 accounts", and the merge card is the
 *  person's own act — confirm (choosing the primary address) or "Not me"; until every login has
 *  confirmed, it says so. Synthetic people and addresses only. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";

import { PersonMergeCard } from "../PersonMergeCard";
import { MentionPicker } from "../../surfaces/MentionUI";
import { accountsLabel, pickerRows } from "../../surfaces/mentions";
import { knownAsMentionable, type KnownPerson } from "../../surfaces/sessionsApi";
import { mergeSentence, type MergeCard, type PersonMe } from "../../surfaces/personApi";

const card = (over: Partial<MergeCard> = {}): MergeCard => ({
  id: "card-1", status: "merge_offered", accounts: 2, confirmed_by_you: false, waiting_on: 2,
  addresses: [{ option: "0", address: "val@work.test", yours: true }, { option: "1", address: "v•••@mail.test", yours: false }],
  primary: null, ...over,
});
const me = (merge: MergeCard | null): PersonMe => ({ person: "p1", accounts: 2, addresses: [], primary: null, merge });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("the picker lists Persons", () => {
  it("shows a person with two logins once, with '2 accounts'", () => {
    const val = { subject: "21", name: "Val Example", email: "val@work.test", accounts: 2, accountsConfirmed: true, vexa: true };
    const rows = pickerRows([val], [], "");
    expect(rows).toHaveLength(1);
    expect(accountsLabel(val)).toBe("2 accounts");
    expect(accountsLabel({})).toBe("");
    // before she confirms: a hint, not a claim
    expect(accountsLabel({ accounts: 2 })).toBe("2 accounts?");
    expect(accountsLabel({ accounts: 2, accounts_confirmed: true })).toBe("2 accounts");
    render(<MentionPicker rows={rows} active={0} onPick={() => {}} />);
    expect(screen.getByText("2 accounts")).toBeTruthy();
  });

  it("carries the count from the known-people set", () => {
    const row: KnownPerson = { key: "p-21", name: "Val Example", subject: "21", vexa: true, how: [], how_words: "",
      last: null, page: null, accounts: 2, accounts_confirmed: false, person: { name: "Val Example", email: "val@work.test" } };
    expect(knownAsMentionable([row])[0].accounts).toBe(2);
    expect(knownAsMentionable([row])[0].accountsConfirmed).toBe(false);
    expect(knownAsMentionable([{ ...row, accounts: undefined }])[0].accounts).toBeUndefined();
  });
});

describe("the merge card", () => {
  it("renders nothing when no card waits", async () => {
    const { container } = render(<PersonMergeCard load={() => Promise.resolve(me(null))} />);
    await waitFor(() => expect(container.innerHTML).toBe(""));
  });

  it("asks, shows the other address masked, and confirms with the chosen primary", async () => {
    const calls: { url: string; body: string }[] = [];
    vi.stubGlobal("fetch", (url: string, init: RequestInit) => {
      calls.push({ url, body: String(init.body) });
      return Promise.resolve({ ok: true, status: 200,
        json: () => Promise.resolve({ status: "confirmed", merge: card({ confirmed_by_you: true, waiting_on: 1 }) }) } as Response);
    });
    let n = 0;
    const load = () => Promise.resolve(me(n++ === 0 ? card() : card({ confirmed_by_you: true, waiting_on: 1 })));
    render(<PersonMergeCard load={load} />);
    expect(await screen.findByText(mergeSentence(card()))).toBeTruthy();
    expect(screen.getByText(/v•••@mail\.test/)).toBeTruthy();
    expect(screen.queryByText(/home/)).toBeNull();
    fireEvent.click(screen.getByLabelText(/v•••@mail\.test/));
    fireEvent.click(screen.getByText("Yes, they are all mine"));
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0].url).toBe("/api/person/merge");
    expect(JSON.parse(calls[0].body)).toEqual({ action: "confirm", card: "card-1", primary: "1" });
    expect(await screen.findByText(/Sign in with your other address/)).toBeTruthy();
  });

  it("'Not me' declines, and nothing merges", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => {
      calls.push(String(init.body));
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "declined", split: 1 }) } as Response);
    });
    render(<PersonMergeCard load={() => Promise.resolve(me(card()))} />);
    fireEvent.click(await screen.findByText("Not me"));
    expect(await screen.findByText(/stay separate/)).toBeTruthy();
    expect(JSON.parse(calls[0])).toEqual({ action: "decline", card: "card-1" });
  });

  it("the last confirmation merges, and opens every workspace to every login", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", (url: string) => {
      calls.push(url);
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ status: "merged", merge: card() }) } as Response);
    });
    render(<PersonMergeCard load={() => Promise.resolve(me(card({ waiting_on: 1 })))} />);
    fireEvent.click(await screen.findByText("Yes, they are all mine"));
    expect(await screen.findByText(/one person now/)).toBeTruthy();
    expect(calls).toEqual(["/api/person/merge", "/api/people/me/accounts-merged"]);
  });
});
