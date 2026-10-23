/** UI-1 People (feature `shell2`): everyone who shares a workspace with you, once each; contacts
 *  apart; you are never listed and read "you"; who is behind an Inbox item; the person panel. */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { companyOf, groupPeople, inScope, mergePeople, type Peer } from "../people";
import { isMe, nameOrYou, setMe } from "../me";
import { whoOf } from "../needs";
import { findPerson } from "../PersonPanel";
import { sanitizeProfile } from "../personProfile";
import { lastChange, PeopleLine } from "../WorkspacePeople";
import type { KnownPerson } from "../../../surfaces/sessionsApi";
import type { Need } from "../../desktop/home2/attentionApi";

afterEach(() => { cleanup(); setMe({}); });

const peer = (subject: string, name: string, email: string, workspaces: string[]): Peer => ({ subject, name, email, workspaces, roles: Object.fromEntries(workspaces.map((w) => [w, "member"])) });
const known = (key: string, name: string, email: string, extra: Partial<KnownPerson> = {}): KnownPerson =>
  ({ key, name, subject: null, vexa: false, how: ["met"], how_words: "met", last: Date.now() / 1000 - 3600, page: null, person: { name, email }, ...extra });

describe("the people you can see", () => {
  it("merges the roster and the interactions set into one row per human, never you", () => {
    setMe({ name: "Ann Lee", email: "ann@acme.example", subject: "u_ann" });
    const ps = mergePeople(
      [peer("u_ann", "Ann Lee", "ann@acme.example", ["acme"]), peer("u_maya", "Maya Hart", "maya@acme.example", ["acme", "garden"])],
      [known("k1", "Maya Hart", "MAYA@acme.example"), known("k2", "Ines Duarte", "ines@brightpine.example")],
      (s) => (s === "u_maya" ? "now" : null));
    expect(ps.map((p) => p.name)).toEqual(["Maya Hart", "Ines Duarte"]);
    expect(ps[0].workspaces).toEqual(["acme", "garden"]);
    expect(ps[0].presence).toBe("now");
    expect(ps[0].aliases).toEqual(["k1"]);
    expect(inScope(ps[0], "workspaces")).toBe(true);
    expect(inScope(ps[1], "contacts")).toBe(true);
    expect(findPerson(ps, "k1")?.name).toBe("Maya Hart");          // opened from the People strip's key
  });
  it("groups All as Recent → My workspaces → Contacts, each once", () => {
    const ps = mergePeople([peer("u_b", "Ben Kim", "ben@acme.example", ["acme"])], [known("k3", "Cleo Lane", "cleo@x.example", { last: null })]);
    expect(groupPeople(ps).map((g) => [g.key, g.people.map((p) => p.name)])).toEqual([["workspaces", ["Ben Kim"]], ["contacts", ["Cleo Lane"]]]);
  });
  it("reads a company from the address, never from a mailbox provider", () => {
    expect(companyOf("ines@brightpine.example")).toBe("Brightpine");
    expect(companyOf("someone@gmail.com")).toBe("");
  });
});

describe("names, not anonymous faces", () => {
  it("you read 'you'", () => {
    setMe({ name: "Ann Lee", email: "ann@acme.example" });
    expect(isMe("ann@acme.example")).toBe(true);
    expect(nameOrYou("Ann Lee")).toBe("you");
    expect(nameOrYou("Maya Hart")).toBe("Maya Hart");
  });
  it("an Inbox item names who is behind it", () => {
    const n = (text: string, by?: string) => ({ id: "x", kind: "mention", workspace: "w", text, at: 1, act: { label: "Open", kind: "open-page", ...(by ? { by } : {}) } }) as unknown as Need;
    expect(whoOf(n("Maya Hart mentioned you in #plot: can you take it?"))).toBe("Maya Hart");
    expect(whoOf(n("Weekly digest failed", "Support desk"))).toBe("Support desk");
    expect(whoOf(n("Weekly digest failed"))).toBe("");
  });
  it("a workspace leads with its people: named faces, who is online, who changed it last", () => {
    setMe({ name: "Ann Lee", subject: "u_ann" });
    render(<PeopleLine members={[{ subject: "u_ann", name: "Ann Lee", initials: "AL", role: "owner", presence: "now" },
      { subject: "u_maya", name: "Maya Hart", initials: "MH", role: "member", presence: "now" }, { subject: "u_leo", name: "Leo Brandt", initials: "LB", role: "member" }]} />);
    expect(document.querySelector("[data-people-line]")?.textContent).toContain("Maya, Leo · 1 online");
    expect(screen.getByRole("button", { name: "Maya Hart" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "you" })).toBeTruthy();
    expect(lastChange({ line: "Updated the roadmap", at: Date.now() - 60_000, by: "Maya" })).toMatch(/^Maya updated the roadmap · /);
  });
});

describe("the S3 profile seam", () => {
  it("keeps only https links of the known kinds and marks fields only you see", () => {
    const p = sanitizeProfile({ claimed: true, headline: "Ops lead", links: [{ kind: "linkedin", url: "https://linkedin.example/in/x" }, { kind: "x", url: "javascript:alert(1)" }, { kind: "myspace", url: "https://m.example" }],
      crm: { org: "Acme", fields: [{ label: "Deal stage", value: "Pilot", only_you: true }] } })!;
    expect(p.links).toEqual([{ kind: "linkedin", url: "https://linkedin.example/in/x" }]);
    expect(p.crm?.fields[0]).toEqual({ label: "Deal stage", value: "Pilot", only_you: true });
    expect(sanitizeProfile(null)).toBeNull();
  });
});
