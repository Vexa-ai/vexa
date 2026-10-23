/** THE PEOPLE ON THE HOME AND IN @ (founder 2026-10-15: "we need to have all people we have
 *  interacted with on the main page"; "all those people should be available as @"; "@ is to tag and
 *  to invite to a call"). One server set; a strip of twelve then "All people (N)" with search and
 *  filters; a click opens a person's page; @ tags by default, invites into the chat only with the
 *  confirm, and a call invitation to someone not on Vexa is a DRAFT — nothing is sent. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { PeopleStrip, STRIP_MAX, filterPeople } from "../PeopleStrip";
import { MentionChoice } from "../../surfaces/MentionUI";
import { callInviteDraft, insertReference, pickerRows } from "../../surfaces/mentions";
import { knownAsMentionable, type KnownPerson } from "../../surfaces/sessionsApi";
import { OPEN_ENTITY_EVENT } from "../../platform";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const person = (i: number, o: Partial<KnownPerson> = {}): KnownPerson => ({
  key: `p-${i}`, name: `Person ${i}`, subject: null, vexa: false, how: ["met"], how_words: "met in a meeting", last: 1000 - i,
  page: null, person: { name: `Person ${i}`, initials: `P${i}`, email: `p${i}@acme.example`, role: "Acme" }, ...o,
});
const ROWS = [person(0, { name: "Valeriia Bushueva", subject: "60", vexa: true, how: ["chat", "workspace"], page: "kg/entities/person/valeriia.md" }),
  ...Array.from({ length: 13 }, (_, i) => person(i + 1))];

describe("the People strip", () => {
  it("twelve faces, then All people (N) with search and a how-you-know-them filter", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ people: ROWS, count: ROWS.length }))));
    const { container } = render(<PeopleStrip />);
    await act(async () => {});
    expect(container.querySelectorAll(".vx-people-faces > [data-person-key]")).toHaveLength(STRIP_MAX);
    fireEvent.click(screen.getByText(`All people (${ROWS.length})`));
    fireEvent.change(screen.getByLabelText("Search people"), { target: { value: "valeriia" } });
    expect(container.querySelectorAll("[data-people-all] [data-person-key]")).toHaveLength(1);
    expect(filterPeople(ROWS, "", "chat").map((r) => r.name)).toEqual(["Valeriia Bushueva"]);
  });
  it("a click opens a person's page; someone with no page opens nothing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ people: ROWS.slice(0, 2), count: 2 }))));
    const opened: unknown[] = [];
    const on = (e: Event) => opened.push((e as CustomEvent).detail);
    window.addEventListener(OPEN_ENTITY_EVENT, on);
    const { container } = render(<PeopleStrip />);
    await act(async () => {});
    fireEvent.click(container.querySelector('[data-person-key="p-0"]')!);
    fireEvent.click(container.querySelector('[data-person-key="p-1"]')!);
    window.removeEventListener(OPEN_ENTITY_EVENT, on);
    expect(opened).toEqual([{ path: "kg/entities/person/valeriia.md" }]);
  });
  it("is not drawn when there is nobody", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ people: [], count: 0 }))));
    const { container } = render(<PeopleStrip />);
    await act(async () => {});
    expect(container.querySelector("[data-people-strip]")).toBeNull();
  });
});

describe("@ — the same people, to tag or to invite", () => {
  const people = knownAsMentionable(ROWS);
  it("the picker searches the same set, by name, address or organisation", () => {
    expect(pickerRows(people, [], "valer").map((r) => r.label)).toEqual(["Valeriia"]);
    expect(pickerRows(people, [], "acme").length).toBeGreaterThan(0);
  });
  it("a Vexa account can be invited into the chat; anyone else only tagged or invited to a call", () => {
    const pick = vi.fn();
    const u = render(<MentionChoice name="Valeriia" vexa onChoose={pick} />);
    expect([...u.container.querySelectorAll("[data-choice]")].map((b) => b.getAttribute("data-choice"))).toEqual(["tag", "chat", "call"]);
    cleanup();
    const n = render(<MentionChoice name="Person 1" vexa={false} onChoose={pick} />);
    expect([...n.container.querySelectorAll("[data-choice]")].map((b) => b.getAttribute("data-choice"))).toEqual(["tag", "call"]);
  });
  it("someone not on Vexa is a reference, and a call invitation is a draft that sends nothing", () => {
    expect(insertReference("hi @val", { start: 3, query: "val" }, people[0]).text).toBe("hi [[Valeriia Bushueva]] ");
    expect(insertReference("hi @per", { start: 3, query: "per" }, people[1]).text).toBe("hi Person 1 ");
    const d = callInviteDraft(people[1]);
    expect(d.say).toBe("Invite Person 1 to this call");
    expect(d.prompt).toMatch(/draft/i);
    expect(d.prompt).toMatch(/send nothing until I give my explicit go/);
  });
});
